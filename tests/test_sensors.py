import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel.config import Settings
from sentinel.main import create_app
from sentinel.sensors import SensorRegistry

OFFICIAL = Path("config/sensors.battledim.json")


def write_json(tmp_path: Path, data: dict[str, object]) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_official_registry_is_the_data_owners_network_element_ids() -> None:
    reg = SensorRegistry.load(OFFICIAL)
    assert reg.provisional is False
    assert reg.counts == {"pressure": 33, "flow": 3, "tank_level": 1}
    assert reg.pressure[:3] == ("n1", "n4", "n31")
    assert reg.flow == ("p227", "p235", "PUMP_1")
    assert reg.tank_level == ("T1",)


def test_provisional_registry_still_loads() -> None:
    reg = SensorRegistry.load(Path("config/sensors.provisional.json"))
    assert reg.provisional is True
    assert reg.counts == {"pressure": 33, "flow": 3, "tank_level": 1}
    assert reg.units is None


def test_optional_units_are_loaded(tmp_path: Path) -> None:
    path = write_json(
        tmp_path,
        {"provisional": False, "pressure": ["n1"], "units": {"pressure": "m"}},
    )
    assert SensorRegistry.load(path).units == {"pressure": "m"}


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    path = write_json(tmp_path, {"provisional": False, "pressure": ["n1"], "flow": ["n1"]})
    with pytest.raises(ValueError, match="duplicate"):
        SensorRegistry.load(path)


def test_registry_without_pressure_sensors_is_rejected(tmp_path: Path) -> None:
    path = write_json(tmp_path, {"provisional": False, "pressure": [], "tank_level": ["T1"]})
    with pytest.raises(ValueError):
        SensorRegistry.load(path)


def test_api_accepts_replay_window_with_official_registry() -> None:
    # Same shape as src/sentinel/data/replay_windows.py writes (official IDs, UTC "Z").
    reg = SensorRegistry.load(OFFICIAL)
    window = {
        "schema_version": "1.0",
        "timestamp": "2019-01-01T00:05:00Z",
        "pressures": dict.fromkeys(reg.pressure, 45.0),
        "flows": dict.fromkeys(reg.flow, 80.0),
        "tank_levels": dict.fromkeys(reg.tank_level, 3.4),
        "source": "replay-2019-v1",
    }
    client = TestClient(create_app(Settings(sensor_registry_path=OFFICIAL)))
    assert client.post("/v1/windows", json=window).status_code == 201
    status = client.get("/v1/status").json()["sensor_registry"]
    assert status["provisional"] is False
    assert status["source"] == str(OFFICIAL)
    assert status["pressure"] == 33


def test_column_style_ids_are_rejected_by_official_registry() -> None:
    # The SCADA CSV column names (Pressure_n1...) are not sensor IDs (docs/data.md).
    reg = SensorRegistry.load(OFFICIAL)
    window = {
        "schema_version": "1.0",
        "timestamp": "2019-01-01T00:05:00Z",
        "pressures": {f"Pressure_{s}": 45.0 for s in reg.pressure},
        "flows": {f"Flow_{s}": 80.0 for s in reg.flow},
        "tank_levels": {f"Level_{s}": 3.4 for s in reg.tank_level},
    }
    client = TestClient(create_app(Settings(sensor_registry_path=OFFICIAL)))
    r = client.post("/v1/windows", json=window)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "sensor_set_mismatch"
