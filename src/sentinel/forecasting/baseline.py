"""Statistical baseline (ML owner): expected pressure = the sensor's hour-of-week profile.

Step 1 of the model ladder (design doc, Table 2): the floor every later model must beat.
The profile is fitted on 2018 (notebook ``p2_forecast/01_baseline``) and saved as JSON;
this module only does inference, in plain Python (no pandas at runtime).
"""

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from sentinel.forecasting.features import IdTranslator, SensorLayout
from sentinel.forecasting.interface import ForecastError
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult

SLOTS = 7 * 24  # hour of week: 0 = Monday 00h, 167 = Sunday 23h
MODEL_NAME = "baseline-hourofweek"


def hour_of_week(ts: datetime) -> int:
    return ts.weekday() * 24 + ts.hour


class HourOfWeekForecaster:
    """Returns, per sensor, the 2018 median for the hour of week of the target timestamp."""

    def __init__(self, profile: Mapping[str, list[float]], version: str = "0.1.0") -> None:
        for sensor, values in profile.items():
            if len(values) != SLOTS:
                raise ValueError(f"profile of {sensor} has {len(values)} hours, expected {SLOTS}")
        self._profile = {s: list(v) for s, v in profile.items()}
        self._ids = IdTranslator(SensorLayout(tuple(self._profile), (), ()))
        self._info = ModelInfo(name=MODEL_NAME, version=version, is_mock=False)

    @classmethod
    def load(cls, path: str | Path) -> "HourOfWeekForecaster":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(profile=data["profile"], version=data["version"])

    @property
    def info(self) -> ModelInfo:
        return self._info

    @property
    def min_history_windows(self) -> int:
        return 1  # uses the calendar only, not the history

    def predict(self, request: ForecastRequest) -> ForecastResult:
        slot = hour_of_week(request.target_timestamp)
        to_model = self._ids.mapping(request.pressure_sensor_ids)
        missing = [s for s, m in to_model.items() if m not in self._profile]
        if missing:
            raise ForecastError(f"sensors without a profile in the model: {missing}")
        return ForecastResult(
            timestamp=request.target_timestamp,
            expected_pressures={s: self._profile[m][slot] for s, m in to_model.items()},
            model=self._info,
        )
