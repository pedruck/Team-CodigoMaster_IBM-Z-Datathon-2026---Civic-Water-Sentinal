"""Forecaster port. Real implementations (baseline, XGBoost/Snap ML, GRU/ONNX) belong to the
ML owner and must satisfy this protocol; see docs/integration-contracts.md §2."""

from typing import Protocol, runtime_checkable

from sentinel.schemas.common import ModelInfo
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult


class ForecastError(Exception):
    """Expected, recoverable forecasting failure (model not loaded, bad input shape...)."""


@runtime_checkable
class Forecaster(Protocol):
    @property
    def info(self) -> ModelInfo: ...

    @property
    def min_history_windows(self) -> int:
        """Minimum contiguous past windows the model needs (e.g. 144 for 12 h)."""
        ...

    def predict(self, request: ForecastRequest) -> ForecastResult:
        """Return expected pressures for every sensor in ``request.pressure_sensor_ids``.

        Must be side-effect free with respect to the request, must not use future data and
        should raise ``ForecastError`` for expected failures.
        """
        ...
