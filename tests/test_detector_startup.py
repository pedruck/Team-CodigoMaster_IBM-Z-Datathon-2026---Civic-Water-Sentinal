"""``SENTINEL_DETECTOR=cusum``: the server starts the CUSUM detector from its calibration file
(BE-012, ADR-007, ADR-008).

Every calibration and region mapping here is synthetic: invented numbers written to ``tmp_path``
with the placeholder sensor IDs of the provisional registry. The tests exercise the startup
wiring and the mechanics of the detector through HTTP. They say nothing about detecting leaks.
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient

from sentinel.config import DEFAULT_REGISTRY_PATH, ConfigError, Settings
from sentinel.detection.calibration import write_artefact
from sentinel.detection.mock import ThresholdMockDetector
from sentinel.forecasting.mock import PersistenceMockForecaster
from sentinel.main import build_pipeline, create_app
from sentinel.schemas.common import ModelInfo
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import window_payload
from tests.conftest import ts
from tests.detection.artefacts import make_artefact

REGISTRY_PATH = Path(__file__).parents[1] / DEFAULT_REGISTRY_PATH
REGISTRY = SensorRegistry.load(REGISTRY_PATH)
# The default forecaster (PersistenceMockForecaster) is the one the artefacts are made for.
PERSISTENCE = {"name": "mock-persistence", "version": "0.1.0"}
# Invented: sigma 0.1 m, so a pressure that stays 0.2 m lower than the persistence forecast
# gives z = 2 per window and S grows by 1.5 until it crosses h = 8 (cap c*h = 16).
CALIBRATION: dict[str, Any] = {"k": 0.5, "h": 8.0, "n_min": 3, "c": 2.0, "h_close": 4.0}
SIGMA_M = 0.1


def write_calibration(
    path: Path, sensors: tuple[str, ...] = REGISTRY.pressure, **fields: Any
) -> Path:
    """An invented artefact with provenance ``synthetic`` for the persistence mock."""
    overrides = {"forecaster": PERSISTENCE, **CALIBRATION, **fields}
    write_artefact(make_artefact(dict.fromkeys(sensors, SIGMA_M), **overrides), path)
    return path


def write_regions(path: Path, regions: dict[str, list[str]]) -> Path:
    path.write_text(json.dumps({"regions": regions}), encoding="utf-8")
    return path


def cusum_env(calibration: Path, regions: Path | None = None) -> dict[str, str]:
    env = {
        "SENTINEL_DETECTOR": "cusum",
        "SENTINEL_DETECTOR_CALIBRATION": str(calibration),
        "SENTINEL_SENSOR_REGISTRY": str(REGISTRY_PATH),
        "SENTINEL_HISTORY_CAPACITY": "6",
        "SENTINEL_MIN_HISTORY_WINDOWS": "3",
    }
    if regions is not None:
        env["SENTINEL_DETECTOR_REGIONS"] = str(regions)
    return env


def test_status_reports_the_cusum_detector_labelled_with_its_calibration_hash(
    tmp_path: Path,
) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")

    with TestClient(create_app(Settings.from_env(cusum_env(calibration)))) as client:
        status = client.get("/v1/status").json()

    digest = hashlib.sha256(calibration.read_bytes()).hexdigest()
    assert status["detector"] == {
        "name": "cusum",
        "version": f"0.1.0+{digest[:8]}",
        "is_mock": True,
    }
    assert status["mocks_active"] is True


class ForecasterStub(PersistenceMockForecaster):
    """Not a model: it only lets a test serve a forecaster with another identity, or one that is
    not a mock (so that ``mocks_active`` can come from the detector alone)."""

    def __init__(self, *, name: str = "mock-persistence", version: str = "0.1.0", is_mock: bool):
        super().__init__()
        self._stub_info = ModelInfo(name=name, version=version, is_mock=is_mock)

    @property
    def info(self) -> ModelInfo:
        return self._stub_info


def test_a_synthetic_calibration_keeps_mocks_active_even_if_the_forecaster_is_not_a_mock(
    tmp_path: Path,
) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    settings = Settings.from_env(cusum_env(calibration))
    pipeline = build_pipeline(settings, forecaster=ForecasterStub(is_mock=False))

    with TestClient(create_app(settings, pipeline)) as client:
        status = client.get("/v1/status").json()

    assert status["forecaster"]["is_mock"] is False
    assert (status["detector"]["name"], status["detector"]["is_mock"]) == ("cusum", True)
    assert status["mocks_active"] is True


def test_an_injected_detector_wins_over_the_settings(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    settings = Settings.from_env(cusum_env(calibration))
    injected = ThresholdMockDetector(sigma_m=1.0, z_threshold=3.0, min_sensors=2)

    assert build_pipeline(settings, detector=injected).detector is injected


# ----------------------------------------------------------------------- startup refuses


def refused(env: dict[str, str], match: str) -> None:
    """The server must not start: ``ConfigError`` from ``create_app``, before any request."""
    with pytest.raises(ConfigError, match=match):
        create_app(Settings.from_env(env))


def test_calibration_made_for_another_forecaster_is_refused(tmp_path: Path) -> None:
    other = {"name": "gru", "version": "2.0.0"}
    calibration = write_calibration(tmp_path / "calibration.synthetic.json", forecaster=other)

    refused(cusum_env(calibration), "gru 2.0.0.*mock-persistence 0.1.0")


def test_calibration_made_for_another_forecaster_version_is_refused(tmp_path: Path) -> None:
    other = {**PERSISTENCE, "version": "0.2.0"}
    calibration = write_calibration(tmp_path / "calibration.synthetic.json", forecaster=other)

    refused(cusum_env(calibration), "mock-persistence 0.2.0.*mock-persistence 0.1.0")


def test_calibration_without_a_registry_sensor_is_refused(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "c.json", sensors=REGISTRY.pressure[:-1])

    refused(cusum_env(calibration), f"missing=.*{REGISTRY.pressure[-1]}")


def test_calibration_with_a_sensor_outside_the_registry_is_refused(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "c.json", sensors=(*REGISTRY.pressure, "P99"))

    refused(cusum_env(calibration), "unexpected=.*P99")


def test_calibration_with_the_registry_sensors_in_another_order_is_refused(
    tmp_path: Path,
) -> None:
    calibration = write_calibration(tmp_path / "c.json", sensors=REGISTRY.pressure[::-1])

    refused(cusum_env(calibration), "different order")


def test_calibration_is_checked_against_the_forecaster_actually_served(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    settings = Settings.from_env(cusum_env(calibration))
    served = ForecasterStub(name="gru", version="2.0.0", is_mock=True)

    with pytest.raises(ConfigError, match=r"mock-persistence 0\.1\.0.*gru 2\.0\.0"):
        build_pipeline(settings, forecaster=served)


def test_errors_of_the_startup_checks_reach_the_caller_unchanged(tmp_path: Path) -> None:
    other = {"name": "gru", "version": "2.0.0"}
    calibration = write_calibration(tmp_path / "calibration.synthetic.json", forecaster=other)

    with pytest.raises(ConfigError) as raised:
        create_app(Settings.from_env(cusum_env(calibration)))

    assert str(raised.value).startswith("calibration was made for forecaster gru 2.0.0")
    assert raised.value.__cause__ is None


def test_missing_calibration_file_is_refused_naming_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-calibration.json"

    refused(cusum_env(missing), re.escape(str(missing)))


def test_calibration_path_that_is_a_directory_is_refused_naming_the_path(tmp_path: Path) -> None:
    refused(cusum_env(tmp_path), re.escape(str(tmp_path)))


@pytest.mark.parametrize("content", ["not json {", "{}", '{"provenance": "synthetic"}'])
def test_invalid_calibration_file_is_refused_naming_the_path(tmp_path: Path, content: str) -> None:
    calibration = tmp_path / "calibration.synthetic.json"
    calibration.write_text(content, encoding="utf-8")

    refused(cusum_env(calibration), re.escape(str(calibration)))


def test_region_mapping_that_does_not_cover_the_registry_is_refused(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    regions = write_regions(tmp_path / "regions.json", {"R-a": list(REGISTRY.pressure[:-1])})

    refused(cusum_env(calibration, regions), f"regions differ.*missing=.*{REGISTRY.pressure[-1]}")


def test_missing_region_mapping_is_refused_naming_the_path(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    missing = tmp_path / "no-such-regions.json"

    refused(cusum_env(calibration, missing), re.escape(str(missing)))


def test_invalid_region_mapping_is_refused_naming_the_path(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    regions = tmp_path / "regions.json"
    regions.write_text('{"regions": {"R-a": ["P01"], "R-b": ["P01"]}}', encoding="utf-8")

    refused(cusum_env(calibration, regions), re.escape(str(regions)))


# ------------------------------------------------------------------- served through HTTP
#
# The default forecaster is the persistence mock: expected = the previous observed pressure. A
# step in the readings is therefore a residual in one window only (ADR-007 §1); a ramp, where
# the pressure keeps falling by the same amount every window, leaves a constant residual that
# the CUSUM accumulates. These tests replay a synthetic ramp to exercise the mechanics (state,
# alert, labels, region) of the wired detector. They are not evidence of detecting leaks.

RAMP_AT = 12  # first window of the ramp; the 12 before it are flat
RAMP_STEP_M = 0.2  # extra drop per window: z about 1.8 per window against sigma 0.1 m
WINDOWS = 30
HALF = len(REGISTRY.pressure) // 2
FIRST_REGION = REGISTRY.pressure[:HALF]
SECOND_REGION = REGISTRY.pressure[HALF:]
RAMPED = SECOND_REGION[:3]  # n_min = 3 sensors, all in the second region


class Replay(NamedTuple):
    states: list[str | None]  # detector_state of every POST /v1/windows response
    alerts: list[dict[str, Any]]  # GET /v1/alerts, oldest first


def replay_ramp(env: dict[str, str]) -> Replay:
    with TestClient(create_app(Settings.from_env(env))) as client:
        responses = [
            client.post(
                "/v1/windows",
                json=window_payload(
                    ts(i),
                    REGISTRY,
                    drop_m=RAMP_STEP_M * max(0, i - RAMP_AT + 1),
                    drop_sensors=RAMPED,
                ),
            )
            for i in range(WINDOWS)
        ]
        alerts = client.get("/v1/alerts").json()["items"]
    assert {r.status_code for r in responses} == {201}
    return Replay(states=[r.json()["detector_state"] for r in responses], alerts=alerts)


def test_a_ramp_opens_one_mock_alert_after_a_normal_state(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")

    run = replay_ramp(cusum_env(calibration))

    assert run.states[:3] == [None] * 3  # three windows of history are required
    assert set(run.states[3:RAMP_AT]) == {"normal"}
    assert run.states[-1] == "alarm"
    (alert,) = run.alerts
    assert alert["is_mock"] is True
    assert alert["models"]["detector"]["name"] == "cusum"
    assert sorted(d["sensor"] for d in alert["deviating_sensors"]) == sorted(RAMPED)
    assert alert["cusum"]["threshold"] == CALIBRATION["h"]
    assert alert["cusum"]["statistic"] > alert["cusum"]["threshold"]
    assert alert["region"] is None  # no region mapping was given
    # S gains about 1.3 per window (z about 1.8 minus k = 0.5) and h = 8: a handful of windows.
    assert ts(RAMP_AT) < datetime.fromisoformat(alert["window_end"]) <= ts(RAMP_AT + 10)


def test_a_region_mapping_names_the_region_of_the_alert(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    # The ramped sensors are in the second region, so the name does not come from the tie-break
    # (ties go to the first region in the file).
    regions = write_regions(
        tmp_path / "regions.json", {"R-a": list(FIRST_REGION), "R-b": list(SECOND_REGION)}
    )

    run = replay_ramp(cusum_env(calibration, regions))

    (alert,) = run.alerts
    assert alert["region"] == "R-b"
