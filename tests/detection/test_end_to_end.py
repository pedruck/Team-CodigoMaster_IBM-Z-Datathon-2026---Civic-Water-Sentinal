"""BE-012a acceptance: synthetic step -> simplified CUSUM -> ``is_mock`` alert, through the API.

Synthetic signal, placeholder sensor IDs and an invented calibration: this exercises the
mechanics only and is not evidence of leak detection. The detector is injected through
``build_pipeline``; selecting it with ``SENTINEL_DETECTOR=cusum`` is not wired yet.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.audit.sinks import NullAuditSink
from sentinel.config import DEFAULT_REGISTRY_PATH, Settings
from sentinel.detection.cusum import CusumDetector
from sentinel.main import build_pipeline, create_app
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import generate, window_payload
from tests.conftest import T0

REGISTRY = SensorRegistry.load(Path(__file__).parents[2] / DEFAULT_REGISTRY_PATH)
STEPPED = ("P05", "P12", "P20")
STEP_AT = 200  # window index; the default settings score from window 144 on


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


@pytest.fixture(scope="module")
def replay(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    artefact = tmp_path_factory.mktemp("calibration") / "calibration.synthetic.json"
    artefact.write_text(
        json.dumps(
            {
                "provenance": "synthetic",
                "sigma_m": dict.fromkeys(REGISTRY.pressure, 0.1),
                "k": 0.5,
                "h": 8.0,
                "n_min": 3,
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
    day = generate(
        T0,
        288,
        REGISTRY,
        seed=1,
        noise_m=0.01,
        leak_at=STEP_AT,
        leak_sensors=STEPPED,
        leak_drop_m=0.5,
    )
    with TestClient(create_app(settings, pipeline)) as client:
        responses = [client.post("/v1/windows", json=w) for w in day]
        assert {r.status_code for r in responses} == {201}
        yield {
            "results": [r.json() for r in responses],
            "alerts": client.get("/v1/alerts").json(),
        }


def test_step_on_three_sensors_opens_exactly_one_alert(replay: dict[str, Any]) -> None:
    assert replay["alerts"]["total"] == 1
    (alert,) = replay["alerts"]["items"]
    # S = 4.5 after the first stepped window, about 9 > h after the second one.
    assert alert["window_end"] == "2019-01-01T16:45:00Z"  # window STEP_AT + 1
    assert sorted(d["sensor"] for d in alert["deviating_sensors"]) == list(STEPPED)


def test_alert_is_labelled_mock_on_the_alert_and_on_the_detector(
    replay: dict[str, Any],
) -> None:
    (alert,) = replay["alerts"]["items"]
    assert alert["models"]["detector"] == {"name": "cusum", "version": "0.1.0", "is_mock": True}
    assert alert["is_mock"] is True


def test_alert_carries_cusum_evidence_above_threshold(replay: dict[str, Any]) -> None:
    (alert,) = replay["alerts"]["items"]
    assert alert["cusum"]["threshold"] == 8.0
    assert alert["cusum"]["statistic"] > alert["cusum"]["threshold"]


def test_detector_is_normal_before_the_step_and_in_alarm_after_the_alert(
    replay: dict[str, Any],
) -> None:
    states = [r["detector_state"] for r in replay["results"]]
    assert states[:144] == [None] * 144  # insufficient history: not scored
    assert set(states[144 : STEP_AT + 1]) == {"normal"}
    assert set(states[STEP_AT + 1 :]) == {"alarm"}
