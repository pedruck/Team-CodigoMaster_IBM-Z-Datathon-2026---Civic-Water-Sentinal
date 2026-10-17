"""CUSUM detector through the API: synthetic days -> ``POST /v1/windows`` -> ``GET /v1/alerts``.

BE-012a: one step opens one ``is_mock`` alert. BE-012b: concurrent alerts on disjoint sensor
groups, release after a step is removed, states and onset (ADR-007 §4-6).

Synthetic signal, placeholder sensor IDs and an invented calibration: this exercises the
mechanics only and is not evidence of leak detection. The detector is injected through
``build_pipeline``; selecting it with ``SENTINEL_DETECTOR=cusum`` is not wired yet.
"""

import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient

from sentinel.audit.sinks import NullAuditSink
from sentinel.config import DEFAULT_REGISTRY_PATH, Settings
from sentinel.detection.cusum import CusumDetector
from sentinel.main import build_pipeline, create_app
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import window_payload
from tests.conftest import ts

REGISTRY = SensorRegistry.load(Path(__file__).parents[2] / DEFAULT_REGISTRY_PATH)
# sigma 0.1 m: a 0.5 m step gives d = 5, so S grows by 4.5 per window and crosses h = 8 on the
# second stepped window; cap c*h = 16; release when S < 4. The default settings score from
# window 144 on.
CALIBRATION = {"k": 0.5, "h": 8.0, "n_min": 3, "c": 2.0, "h_close": 4.0}
STEP_M = 0.5
GROUP_A = ("P05", "P12", "P20")
GROUP_B = ("P01", "P02", "P03")
STEP_AT = 200


class Day(NamedTuple):
    states: list[str | None]  # detector_state of every POST /v1/windows response
    alerts: list[dict[str, Any]]  # GET /v1/alerts, oldest first


class ProfileForecaster:
    """Test double: expected = the noise-free synthetic profile. Unlike
    ``PersistenceMockForecaster`` it does not follow the step, so the residual stays
    displaced (ADR-007 §1)."""

    def __init__(self, registry: SensorRegistry) -> None:
        self._registry = registry
        self._info = ModelInfo(name="test-profile", version="0.1.0", is_mock=True)

    @property
    def info(self) -> ModelInfo:
        return self._info

    @property
    def min_history_windows(self) -> int:
        return 1

    def predict(self, request: ForecastRequest) -> ForecastResult:
        profile = window_payload(request.target_timestamp, self._registry)["pressures"]
        return ForecastResult(
            timestamp=request.target_timestamp,
            expected_pressures={s: profile[s] for s in request.pressure_sensor_ids},
            model=self._info,
        )


def replay_day(
    tmp_path_factory: pytest.TempPathFactory,
    stepped: Callable[[int], tuple[str, ...]],
    noise_m: float,
) -> Day:
    """Replay 288 windows; ``stepped(i)`` names the sensors reading ``STEP_M`` low at window i."""
    artefact = tmp_path_factory.mktemp("calibration") / "calibration.synthetic.json"
    artefact.write_text(
        json.dumps(
            {
                "provenance": "synthetic",
                "sigma_m": dict.fromkeys(REGISTRY.pressure, 0.1),
                **CALIBRATION,
            }
        ),
        encoding="utf-8",
    )
    settings = Settings()
    pipeline = build_pipeline(
        settings,
        registry=REGISTRY,
        forecaster=ProfileForecaster(REGISTRY),
        detector=CusumDetector.from_file(artefact),
        audit_sink=NullAuditSink(),
    )
    rng = random.Random(1)
    windows = [
        window_payload(
            ts(i), REGISTRY, noise_m=noise_m, drop_m=STEP_M, drop_sensors=stepped(i), rng=rng
        )
        for i in range(288)
    ]
    with TestClient(create_app(settings, pipeline)) as client:
        responses = [client.post("/v1/windows", json=w) for w in windows]
        assert {r.status_code for r in responses} == {201}
        alerts = client.get("/v1/alerts").json()["items"]
    return Day(
        states=[r.json()["detector_state"] for r in responses],
        alerts=sorted(alerts, key=lambda a: a["window_end"]),
    )


def iso(i: int) -> str:
    return ts(i).isoformat().replace("+00:00", "Z")


def sensors(alert: dict[str, Any]) -> list[str]:
    return sorted(d["sensor"] for d in alert["deviating_sensors"])


# --------------------------------------------------------------------------- BE-012a


@pytest.fixture(scope="module")
def one_step(tmp_path_factory: pytest.TempPathFactory) -> Day:
    return replay_day(tmp_path_factory, lambda i: GROUP_A if i >= STEP_AT else (), noise_m=0.01)


def test_step_on_three_sensors_opens_exactly_one_alert(one_step: Day) -> None:
    (alert,) = one_step.alerts
    assert alert["window_end"] == "2019-01-01T16:45:00Z"  # window STEP_AT + 1
    assert sensors(alert) == sorted(GROUP_A)


def test_alert_is_labelled_mock_on_the_alert_and_on_the_detector(one_step: Day) -> None:
    (alert,) = one_step.alerts
    assert alert["models"]["detector"] == {"name": "cusum", "version": "0.1.0", "is_mock": True}
    assert alert["is_mock"] is True


def test_alert_carries_cusum_evidence_above_threshold(one_step: Day) -> None:
    (alert,) = one_step.alerts
    assert alert["cusum"]["threshold"] == 8.0
    assert alert["cusum"]["statistic"] > alert["cusum"]["threshold"]


def test_detector_is_normal_before_the_step_and_in_alarm_after_the_alert(one_step: Day) -> None:
    states = one_step.states
    assert states[:144] == [None] * 144  # insufficient history: not scored
    assert set(states[144 : STEP_AT + 1]) == {"normal"}
    assert set(states[STEP_AT + 1 :]) == {"alarm"}


# --------------------------------------------------------------------------- BE-012b


@pytest.fixture(scope="module")
def two_groups(tmp_path_factory: pytest.TempPathFactory) -> Day:
    def stepped(i: int) -> tuple[str, ...]:
        return (GROUP_A if i >= 160 else ()) + (GROUP_B if i >= 180 else ())

    return replay_day(tmp_path_factory, stepped, noise_m=0.01)


def test_steps_on_disjoint_groups_open_one_alert_each(two_groups: Day) -> None:
    first, second = two_groups.alerts
    assert (first["window_end"], sensors(first)) == (iso(161), sorted(GROUP_A))
    assert (second["window_end"], sensors(second)) == (iso(181), sorted(GROUP_B))


def test_onset_of_each_alert_is_the_last_window_before_its_step(two_groups: Day) -> None:
    first, second = two_groups.alerts
    assert (first["suspected_onset"], first["hours_since_onset"]) == (iso(159), 0.17)
    assert (second["suspected_onset"], second["hours_since_onset"]) == (iso(179), 0.17)


@pytest.fixture(scope="module")
def step_removed(tmp_path_factory: pytest.TempPathFactory) -> Day:
    # Noise-free so the release window is exact: S = 16 at the cap on the last stepped window
    # (199), then 16 - 0.5 j on the j-th normal window; S < h_close = 4 first on the 25th
    # (window 224). Without the cap S would be about 180 and no release would happen today.
    return replay_day(
        tmp_path_factory, lambda i: GROUP_A if 160 <= i < 200 or i >= 226 else (), noise_m=0.0
    )


def test_sensors_are_released_within_the_cap_bound_and_alert_again(step_removed: Day) -> None:
    first, second = step_removed.alerts
    assert (first["window_end"], sensors(first)) == (iso(161), sorted(GROUP_A))
    assert (second["window_end"], sensors(second)) == (iso(227), sorted(GROUP_A))


def test_state_returns_to_normal_between_the_two_episodes(step_removed: Day) -> None:
    states = step_removed.states
    assert set(states[144:161]) == {"normal"}
    assert set(states[161:224]) == {"alarm"}
    assert set(states[224:227]) == {"normal"}
    assert set(states[227:]) == {"alarm"}


@pytest.fixture(scope="module")
def two_sensors_only(tmp_path_factory: pytest.TempPathFactory) -> Day:
    return replay_day(tmp_path_factory, lambda i: GROUP_A[:2] if i >= STEP_AT else (), noise_m=0.01)


def test_fewer_than_n_sensors_above_h_is_suspect_without_alert(two_sensors_only: Day) -> None:
    assert two_sensors_only.alerts == []
    assert set(two_sensors_only.states[144 : STEP_AT + 1]) == {"normal"}
    assert set(two_sensors_only.states[STEP_AT + 1 :]) == {"suspect"}
