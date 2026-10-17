"""CUSUM detector through the API: synthetic days -> ``POST /v1/windows`` -> ``GET /v1/alerts``.

BE-012a: one step opens one ``is_mock`` alert. BE-012b: concurrent alerts on disjoint sensor
groups, release after a step is removed, states and onset (ADR-007 §4-6). BE-012c: a
calibration that does not match the registry fails detection, not ingestion.

BE-012d: the artefact is written by ``calibrate()`` + ``write_artefact()`` and the server starts
with ``CusumDetector.for_pipeline``.

Synthetic signal, placeholder sensor IDs and an invented calibration: this exercises the
mechanics only and is not evidence of leak detection. The detector is injected through
``build_pipeline`` here; the selection with ``SENTINEL_DETECTOR=cusum`` is tested in
``tests/test_detector_startup.py``.
"""

import hashlib
import json
import random
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient

from sentinel.audit.sinks import NullAuditSink
from sentinel.config import DEFAULT_REGISTRY_PATH, Settings
from sentinel.detection.calibration import ForecasterIdentity, calibrate, write_artefact
from sentinel.detection.cusum import CusumDetector
from sentinel.main import build_pipeline, create_app
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import window_payload
from tests.conftest import INTERVAL, ts
from tests.detection.artefacts import (
    FORECASTER_NAME,
    FORECASTER_VERSION,
    INVENTED_FOLDS_SHA256,
    INVENTED_RESIDUALS_SHA256,
    artefact_fields,
)
from tests.detection.residuals import synthetic_residuals

REGISTRY = SensorRegistry.load(Path(__file__).parents[2] / DEFAULT_REGISTRY_PATH)
# sigma 0.1 m: a 0.5 m step gives d = 5, so S grows by 4.5 per window and crosses h = 8 on the
# second stepped window; cap c*h = 16; release when S < 4. The default settings score from
# window 144 on.
CALIBRATION: dict[str, Any] = {"k": 0.5, "h": 8.0, "n_min": 3, "c": 2.0, "h_close": 4.0}
STEP_M = 0.5
GROUP_A = ("P05", "P12", "P20")
GROUP_B = ("P01", "P02", "P03")
STEP_AT = 200


class Day(NamedTuple):
    artefact_sha256: str  # hash of the calibration file the server started with
    states: list[str | None]  # detector_state of every POST /v1/windows response
    alerts: list[dict[str, Any]]  # GET /v1/alerts, oldest first


class ProfileForecaster:
    """Test double: expected = the noise-free synthetic profile. Unlike
    ``PersistenceMockForecaster`` it does not follow the step, so the residual stays
    displaced (ADR-007 §1)."""

    def __init__(self, registry: SensorRegistry) -> None:
        self._registry = registry
        self._info = ModelInfo(name=FORECASTER_NAME, version=FORECASTER_VERSION, is_mock=True)

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
    indices: Sequence[int] = range(288),
    settings: Settings | None = None,
    calibrated: bool = False,
) -> Day:
    """Replay the windows ``indices`` (default: one day); ``stepped(i)`` names the sensors
    reading ``STEP_M`` low at window i. ``states`` follow the order of ``indices``.

    By default the artefact has sigma 0.1 m for every sensor and is loaded with ``from_file``.
    With ``calibrated`` it is the output of ``calibrate()`` on synthetic residuals (true sigma
    about 0.1 m), written by ``write_artefact`` and started as the server does."""
    artefact = tmp_path_factory.mktemp("calibration") / "calibration.synthetic.json"
    settings = settings or Settings()
    forecaster = ProfileForecaster(REGISTRY)
    if calibrated:
        residuals = synthetic_residuals(
            dict.fromkeys(REGISTRY.pressure, 0.1),
            windows=10 * 288,
            seed=11,
            phi=0.9,
            amplitude=0.0,
            common_scale=0.0,
        )
        write_artefact(
            calibrate(
                residuals,
                provenance="synthetic",
                forecaster=ForecasterIdentity(
                    name=forecaster.info.name, version=forecaster.info.version
                ),
                residuals_sha256=INVENTED_RESIDUALS_SHA256,
                folds_sha256=INVENTED_FOLDS_SHA256,
                sigma_floor_m=0.01,
                **CALIBRATION,
            ),
            artefact,
        )
        detector = CusumDetector.for_pipeline(
            artefact,
            interval=INTERVAL,
            pressure_sensors=REGISTRY.pressure,
            forecaster=forecaster.info,
        )
    else:
        fields = artefact_fields(dict.fromkeys(REGISTRY.pressure, 0.1), **CALIBRATION)
        artefact.write_text(json.dumps(fields), encoding="utf-8")
        detector = CusumDetector.from_file(artefact, interval=INTERVAL)
    pipeline = build_pipeline(
        settings,
        registry=REGISTRY,
        forecaster=forecaster,
        detector=detector,
        audit_sink=NullAuditSink(),
    )
    rng = random.Random(1)
    windows = [
        window_payload(
            ts(i), REGISTRY, noise_m=noise_m, drop_m=STEP_M, drop_sensors=stepped(i), rng=rng
        )
        for i in indices
    ]
    with TestClient(create_app(settings, pipeline)) as client:
        responses = [client.post("/v1/windows", json=w) for w in windows]
        assert {r.status_code for r in responses} == {201}
        alerts = client.get("/v1/alerts").json()["items"]
    return Day(
        artefact_sha256=hashlib.sha256(artefact.read_bytes()).hexdigest(),
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
    version = f"0.1.0+{one_step.artefact_sha256[:8]}"
    assert alert["models"]["detector"] == {"name": "cusum", "version": version, "is_mock": True}
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


@pytest.fixture(scope="module")
def calibrated_step(tmp_path_factory: pytest.TempPathFactory) -> Day:
    return replay_day(
        tmp_path_factory,
        lambda i: GROUP_A if i >= STEP_AT else (),
        noise_m=0.01,
        calibrated=True,
    )


def test_artefact_from_calibrate_serves_a_step_as_exactly_one_alert(calibrated_step: Day) -> None:
    (alert,) = calibrated_step.alerts
    assert alert["window_end"] == iso(STEP_AT + 1)
    assert sensors(alert) == sorted(GROUP_A)
    assert alert["cusum"]["threshold"] == 8.0


def test_calibrated_server_labels_the_alert_with_the_hash_of_the_written_file(
    calibrated_step: Day,
) -> None:
    (alert,) = calibrated_step.alerts
    version = f"0.1.0+{calibrated_step.artefact_sha256[:8]}"
    assert alert["models"]["detector"] == {"name": "cusum", "version": version, "is_mock": True}
    assert alert["is_mock"] is True


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


# --------------------------------------------------------------------------- BE-012c


def test_calibration_without_a_registry_sensor_fails_detection_not_ingestion(
    tmp_path: Path,
) -> None:
    artefact = tmp_path / "calibration.synthetic.json"
    sigma = dict.fromkeys(REGISTRY.pressure[:-1], 0.1)  # one pressure sensor missing
    artefact.write_text(json.dumps(artefact_fields(sigma, **CALIBRATION)), encoding="utf-8")
    settings = Settings(history_capacity=6, min_history_windows=3)
    pipeline = build_pipeline(
        settings,
        registry=REGISTRY,
        forecaster=ProfileForecaster(REGISTRY),
        detector=CusumDetector.from_file(artefact, interval=INTERVAL),
        audit_sink=NullAuditSink(),
    )
    with TestClient(create_app(settings, pipeline)) as client:
        responses = [
            client.post("/v1/windows", json=window_payload(ts(i), REGISTRY)) for i in range(5)
        ]
        alerts = client.get("/v1/alerts").json()["total"]

    scored = [r.json() for r in responses[3:]]
    assert {r.status_code for r in responses} == {201}
    assert [r["status"] for r in scored] == ["detection_failed"] * 2
    assert {i["code"] for r in scored for i in r["issues"]} == {"DetectionError"}
    assert alerts == 0


SHORT_HISTORY = Settings(history_capacity=6, min_history_windows=3)


def gap_day(
    tmp_path_factory: pytest.TempPathFactory, resume_at: int
) -> tuple[dict[int, str | None], list[dict[str, Any]]]:
    """Group A low from window 3 on; windows 9 .. resume_at - 1 never arrive. With 3 windows
    of history the pipeline scores again from resume_at + 3."""
    indices = [*range(9), *range(resume_at, resume_at + 12)]
    day = replay_day(
        tmp_path_factory,
        lambda i: GROUP_A if i >= 3 else (),
        noise_m=0.0,
        indices=indices,
        settings=SHORT_HISTORY,
    )
    return dict(zip(indices, day.states, strict=True)), day.alerts


def test_leak_continuing_across_a_short_gap_keeps_its_alert(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    # scored again at 22: 13 missing windows, S = 16 - 6.5 = 9.5 >= h_close, still claimed
    states, alerts = gap_day(tmp_path_factory, resume_at=19)

    assert [a["window_end"] for a in alerts] == [iso(4)]
    assert [states[i] for i in (22, 25, 30)] == ["alarm"] * 3


def test_long_gap_empties_cusum_and_a_continuing_leak_alerts_again(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    # scored again at 52: 43 missing windows empty S (16 - 21.5 < 0), sensors released
    states, alerts = gap_day(tmp_path_factory, resume_at=49)

    assert [a["window_end"] for a in alerts] == [iso(4), iso(53)]
    assert [states[i] for i in (52, 53)] == ["normal", "alarm"]
