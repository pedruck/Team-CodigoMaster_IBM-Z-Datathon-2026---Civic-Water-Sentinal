"""Test double. NOT a forecasting model: it repeats the last observed pressure."""

from sentinel.schemas.common import ModelInfo
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult


class PersistenceMockForecaster:
    """Expected pressure = previous window's pressure. Exists only to exercise the pipeline."""

    def __init__(self, min_history_windows: int = 1) -> None:
        self._min_history = min_history_windows
        self._info = ModelInfo(name="mock-persistence", version="0.1.0", is_mock=True)

    @property
    def info(self) -> ModelInfo:
        return self._info

    @property
    def min_history_windows(self) -> int:
        return self._min_history

    def predict(self, request: ForecastRequest) -> ForecastResult:
        last = request.history[-1]
        return ForecastResult(
            timestamp=request.target_timestamp,
            expected_pressures={s: last.pressures[s] for s in request.pressure_sensor_ids},
            model=self._info,
        )
