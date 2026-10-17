import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentinel.forecasting.baseline import SLOTS, HourOfWeekForecaster, hour_of_week
from sentinel.forecasting.interface import Forecaster, ForecastError
from sentinel.forecasting.registry import load_forecaster
from tests.forecasting_helpers import LAYOUT, T0, request


def _model() -> HourOfWeekForecaster:
    profile = {s: [float(h) + i for h in range(SLOTS)] for i, s in enumerate(LAYOUT.pressure)}
    return HourOfWeekForecaster(profile)


def test_satisfies_forecaster_port() -> None:
    assert isinstance(_model(), Forecaster)


def test_hour_of_week_bounds() -> None:
    assert hour_of_week(datetime(2018, 1, 1, 0, 5, tzinfo=UTC)) == 0  # Monday
    assert hour_of_week(datetime(2018, 1, 7, 23, 55, tzinfo=UTC)) == SLOTS - 1  # Sunday


def test_predict_uses_profile_of_target_hour() -> None:
    result = _model().predict(request(T0))  # Thursday 06h -> slot 3*24+6 = 78
    assert result.timestamp == T0
    assert result.expected_pressures == {
        "Pressure_n1": 78.0,
        "Pressure_n4": 79.0,
        "Pressure_n31": 80.0,
    }
    assert result.model.is_mock is False


def test_unknown_sensor_raises_forecast_error() -> None:
    with pytest.raises(ForecastError):
        _model().predict(request(T0, sensors=("Pressure_n1", "Pressure_n999")))


def test_profile_with_wrong_length_is_rejected() -> None:
    with pytest.raises(ValueError):
        HourOfWeekForecaster({"Pressure_n1": [1.0, 2.0]})


def test_load_through_registry(tmp_path: Path) -> None:
    path = tmp_path / "baseline-hourofweek@0.1.0.json"
    path.write_text(json.dumps({"version": "0.1.0", "profile": {"Pressure_n1": [1.0] * SLOTS}}))
    model = load_forecaster("baseline", path)
    assert model.info.version == "0.1.0"


def test_registry_rejects_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_forecaster("random-forest", tmp_path)
