from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel.config import Settings
from sentinel.main import create_app
from sentinel.sensors import SensorRegistry

HEADER = "sensor_id,tipo,unidade,elemento_rede,papel\n"


def write_csv(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sensors.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def test_csv_from_data_owner_is_loaded_in_file_order(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "Pressure_n1,pressao,m,n1,\n"
        "Pressure_n4,pressao,m,n4,\n"
        "Flow_p227,vazao,m3/h,p227,entrada\n"
        "Flow_PUMP_1,vazao,m3/h,PUMP_1,bomba\n"
        "Level_T1,nivel,m,T1,tanque\n",
    )
    reg = SensorRegistry.load(path)
    assert reg.pressure == ("Pressure_n1", "Pressure_n4")
    assert reg.flow == ("Flow_p227", "Flow_PUMP_1")
    assert reg.tank_level == ("Level_T1",)
    assert reg.provisional is False
    assert reg.units == {"pressure": "m", "flow": "m3/h", "tank_level": "m"}
    assert reg.source == str(path)


def test_csv_with_unknown_type_is_rejected(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "Pressure_n1,pressao,m,n1,\nX1,temperatura,C,x,\n")
    with pytest.raises(ValueError, match="temperatura"):
        SensorRegistry.load(path)


def test_csv_with_duplicate_ids_is_rejected(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "Pressure_n1,pressao,m,n1,\nPressure_n1,pressao,m,n1,\n")
    with pytest.raises(ValueError, match="duplicate"):
        SensorRegistry.load(path)


def test_csv_with_mixed_units_per_type_is_rejected(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "Pressure_n1,pressao,m,n1,\nPressure_n4,pressao,bar,n4,\n")
    with pytest.raises(ValueError, match="unit"):
        SensorRegistry.load(path)


def test_csv_without_pressure_sensors_is_rejected(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "Level_T1,nivel,m,T1,tanque\n")
    with pytest.raises(ValueError):
        SensorRegistry.load(path)


def test_provisional_json_still_loads() -> None:
    reg = SensorRegistry.load(Path("config/sensors.provisional.json"))
    assert reg.provisional is True
    assert reg.counts == {"pressure": 33, "flow": 3, "tank_level": 1}
    assert reg.units is None


def test_api_accepts_real_shaped_window_with_csv_registry(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "Pressure_n1,pressao,m,n1,\nFlow_p227,vazao,m3/h,p227,entrada\nLevel_T1,nivel,m,T1,tanque\n",
    )
    client = TestClient(create_app(Settings(sensor_registry_path=path)))
    window = {
        "schema_version": "1.0",
        "timestamp": "2019-01-01T00:05:00Z",
        "pressures": {"Pressure_n1": 28.66},
        "flows": {"Flow_p227": 82.9},
        "tank_levels": {"Level_T1": 3.43},
    }
    assert client.post("/v1/windows", json=window).status_code == 201
    registry = client.get("/v1/status").json()["sensor_registry"]
    assert registry["provisional"] is False
    assert registry["units"] == {"pressure": "m", "flow": "m3/h", "tank_level": "m"}
    assert registry["pressure"] == 1
