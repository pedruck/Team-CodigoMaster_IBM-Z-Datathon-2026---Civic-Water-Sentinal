"""BE-011: the API loads the forecaster named by ``SENTINEL_FORECASTER`` at startup (ADR-005)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.config import DEFAULT_REGISTRY_PATH, ConfigError, Settings
from sentinel.detection.calibration import write_artefact
from sentinel.forecasting.baseline import SLOTS
from sentinel.forecasting.integrity import artefact_sha256
from sentinel.main import create_app
from sentinel.sensors import SensorRegistry
from tests.detection.artefacts import make_artefact

OFFICIAL = Path("config/sensors.battledim.json")
T0 = datetime(2019, 1, 1, 0, 5, tzinfo=UTC)


def _baseline_artefact(tmp_path: Path, pressure: tuple[str, ...], level: float = 50.0) -> Path:
    path = tmp_path / "baseline-hourofweek@0.0.1.json"
    profile = {s: [level] * SLOTS for s in pressure}
    path.write_text(json.dumps({"version": "0.0.1", "profile": profile}), encoding="utf-8")
    return path


def _window(reg: SensorRegistry, i: int, pressure: float = 50.0) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "timestamp": (T0 + timedelta(minutes=5 * i)).isoformat(),
        "pressures": dict.fromkeys(reg.pressure, pressure),
        "flows": dict.fromkeys(reg.flow, 80.0),
        "tank_levels": dict.fromkeys(reg.tank_level, 3.4),
    }


def test_real_forecaster_needs_an_artefact_path() -> None:
    with pytest.raises(ConfigError, match="SENTINEL_FORECASTER_PATH"):
        Settings.from_env({"SENTINEL_FORECASTER": "xgboost"})


@pytest.mark.parametrize(
    "env",
    [
        {"SENTINEL_FORECASTER": "lstm", "SENTINEL_FORECASTER_PATH": "/m"},
        {"SENTINEL_FORECASTER_ENGINE": "onnx"},
        {"SENTINEL_FORECASTER_CONTRIBUTIONS": "sometimes"},
        {"SENTINEL_FORECASTER_TREE_FORMAT": "compressed_trees"},  # typo: fails before Snap ML
        {"SENTINEL_FORECASTER": "xgboost", "SENTINEL_FORECASTER_PATH": ""},
    ],
)
def test_invalid_forecaster_settings_fail_at_startup(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env)


@pytest.mark.parametrize(
    ("raw", "mode"),
    [(None, "on_alert"), ("true", "all"), ("all", "all"), ("on_alert", "on_alert"), ("no", "off")],
)
def test_contributions_switch_accepts_modes_and_the_old_booleans(
    raw: str | None, mode: str
) -> None:
    env = {} if raw is None else {"SENTINEL_FORECASTER_CONTRIBUTIONS": raw}
    assert Settings.from_env(env).forecaster_contributions == mode


def test_forecaster_settings_are_parsed() -> None:
    s = Settings.from_env(
        {
            "SENTINEL_FORECASTER": "xgboost",
            "SENTINEL_FORECASTER_PATH": "/models/xgboost-33@0.1.0",
            "SENTINEL_FORECASTER_CONTRIBUTIONS": "false",
            "SENTINEL_FORECASTER_ENGINE": "xgboost",
            "SENTINEL_FORECASTER_TREE_FORMAT": "compress_trees",
        }
    )
    assert s.forecaster_path == Path("/models/xgboost-33@0.1.0")
    assert (s.forecaster_contributions, s.forecaster_engine, s.forecaster_tree_format) == (
        "off",
        "xgboost",
        "compress_trees",
    )


def test_missing_artefact_stops_startup_instead_of_falling_back_to_mock(tmp_path: Path) -> None:
    settings = Settings(forecaster="baseline", forecaster_path=tmp_path / "absent.json")
    with pytest.raises(FileNotFoundError):
        create_app(settings)


def test_api_scores_with_the_configured_baseline(tmp_path: Path) -> None:
    reg = SensorRegistry.load(OFFICIAL)
    artefact = _baseline_artefact(tmp_path, reg.pressure)
    settings = Settings.from_env(
        {
            "SENTINEL_SENSOR_REGISTRY": str(OFFICIAL),
            "SENTINEL_FORECASTER": "baseline",
            "SENTINEL_FORECASTER_PATH": str(artefact),
            "SENTINEL_MIN_HISTORY_WINDOWS": "1",
            "SENTINEL_AUDIT_SINK": "memory",
        }
    )
    client = TestClient(create_app(settings))

    status = client.get("/v1/status").json()
    assert status["forecaster"] == {
        "name": "baseline-hourofweek",
        "version": f"0.0.1+{artefact_sha256(artefact)[:8]}",  # the digest names the weights
        "is_mock": False,
    }
    assert status["mocks_active"] is True  # the detector is still the mock

    assert client.post("/v1/windows", json=_window(reg, 0)).json()["status"] == (
        "insufficient_history"
    )
    # 5 m below the baseline's 50 m on every sensor: the mock detector (sigma 1 m, z >= 3) fires,
    # and the alert carries the real forecaster's expectation.
    result = client.post("/v1/windows", json=_window(reg, 1, pressure=45.0)).json()
    assert result["status"] == "scored"
    assert result["forecaster"]["name"] == "baseline-hourofweek"
    assert result["alert_ids"]
    alert = client.get(f"/v1/alerts/{result['alert_ids'][0]}").json()
    assert alert["models"]["forecaster"]["is_mock"] is False
    assert alert["is_mock"] is True
    assert {s["deviation_m"] for s in alert["deviating_sensors"]} == {-5.0}


def test_api_loads_xgboost_with_contributions_switch(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    pytest.importorskip("xgboost")
    # Imported here: that module skips itself at import time without numpy.
    from tests.test_forecasting_ml import _train_xgb  # noqa: PLC0415

    model_dir = _train_xgb(tmp_path)
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "provisional": False,
                "pressure": ["n1", "n4", "n31"],
                "flow": ["p227", "p235", "PUMP_1"],
                "tank_level": ["T1"],
            }
        ),
        encoding="utf-8",
    )
    reg = SensorRegistry.load(registry)
    for contributions in ("all", "on_alert", "off"):
        settings = Settings(
            sensor_registry_path=registry,
            forecaster="xgboost",
            forecaster_path=model_dir,
            forecaster_engine="xgboost",
            forecaster_contributions=contributions,
            min_history_windows=1,
        )
        client = TestClient(create_app(settings))
        assert client.get("/v1/status").json()["forecaster"]["name"] == "xgboost-33"
        client.post("/v1/windows", json=_window(reg, 0))
        result = client.post("/v1/windows", json=_window(reg, 1)).json()
        assert result["status"] == "scored", result["issues"]
        assert result["issues"] == []


def _cusum_with_baseline_env(tmp_path: Path, calibrated_for: dict[str, str]) -> dict[str, str]:
    """Real baseline forecaster + CUSUM detector, both selected from the environment. The
    calibration is invented (``provenance = synthetic``); only the startup check matters."""
    reg = SensorRegistry.load(DEFAULT_REGISTRY_PATH)
    calibration = tmp_path / "calibration.synthetic.json"
    artefact = make_artefact(dict.fromkeys(reg.pressure, 0.1), forecaster=calibrated_for)
    write_artefact(artefact, calibration)
    return {
        "SENTINEL_FORECASTER": "baseline",
        "SENTINEL_FORECASTER_PATH": str(_baseline_artefact(tmp_path, reg.pressure)),
        "SENTINEL_DETECTOR": "cusum",
        "SENTINEL_DETECTOR_CALIBRATION": str(calibration),
        "SENTINEL_MIN_HISTORY_WINDOWS": "1",
    }


def test_cusum_starts_with_the_real_forecaster_it_was_calibrated_for(tmp_path: Path) -> None:
    env = _cusum_with_baseline_env(tmp_path, {"name": "baseline-hourofweek", "version": "0.0.1"})
    reg = SensorRegistry.load(DEFAULT_REGISTRY_PATH)
    client = TestClient(create_app(Settings.from_env(env)))

    status = client.get("/v1/status").json()
    assert status["forecaster"]["name"] == "baseline-hourofweek"
    assert status["detector"]["name"] == "cusum"
    client.post("/v1/windows", json=_window(reg, 0))
    result = client.post("/v1/windows", json=_window(reg, 1)).json()
    assert result["status"] == "scored", result["issues"]
    assert result["detector"]["name"] == "cusum"


def test_cusum_calibrated_for_another_forecaster_stops_startup(tmp_path: Path) -> None:
    # Calibrated on the persistence mock's residuals, served with the baseline: refused.
    env = _cusum_with_baseline_env(tmp_path, {"name": "mock-persistence", "version": "0.1.0"})
    with pytest.raises(ConfigError, match="forecaster"):
        create_app(Settings.from_env(env))
