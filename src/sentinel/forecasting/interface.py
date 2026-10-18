"""Forecaster port. Real implementations (baseline, XGBoost/Snap ML, GRU/ONNX) belong to the
ML owner and must satisfy this protocol; see docs/integration-contracts.md §2."""

from collections.abc import Sequence
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


@runtime_checkable
class ExplainingForecaster(Protocol):
    """Optional extension: contributions computed on demand, after detection.

    A forecaster whose explanation is expensive (XGBoost TreeSHAP, ~127 ms per window on the
    VM) can leave ``ForecastResult.contributions`` empty and explain only the sensors of an
    alert. The pipeline calls ``explain`` once per alert, with the same request it gave to
    ``predict``; ``None`` means the forecaster does not explain (contributions switched off).
    """

    def explain(
        self, request: ForecastRequest, sensor_ids: Sequence[str]
    ) -> dict[str, dict[str, float]] | None: ...
