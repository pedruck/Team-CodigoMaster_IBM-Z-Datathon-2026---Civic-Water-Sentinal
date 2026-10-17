"""Forecaster contract (``sentinel.forecast`` v1.0). See docs/integration-contracts.md §2."""

from pydantic import Field

from sentinel.schemas.common import ContractModel, FiniteFloat, ModelInfo, UtcDatetime
from sentinel.schemas.windows import SensorId, SensorWindow


class ForecastRequest(ContractModel):
    """Everything a forecaster may use to predict the pressures at ``target_timestamp``.

    Guarantees given by the backend:
    - ``history`` is ascending, contiguous (no gaps) and its last window ends exactly one
      interval before ``target_timestamp``; it never contains the target window.
    - ``len(history) >= max(settings.min_history_windows, forecaster.min_history_windows)``.
    - ``current`` is the window being scored. A forecaster MUST NOT read
      ``current.pressures[s]`` to predict sensor ``s`` (it is the observation under test).
    """

    target_timestamp: UtcDatetime
    pressure_sensor_ids: tuple[SensorId, ...] = Field(
        description="Sensors to predict, in registry order (use it if the model needs arrays)."
    )
    history: tuple[SensorWindow, ...]
    current: SensorWindow


class ForecastResult(ContractModel):
    timestamp: UtcDatetime = Field(description="Must equal ForecastRequest.target_timestamp.")
    expected_pressures: dict[SensorId, FiniteFloat] = Field(
        description="Expected pressure per sensor, same unit as the input pressures (m)."
    )
    model: ModelInfo
    contributions: dict[SensorId, dict[str, FiniteFloat]] | None = Field(
        default=None,
        description="Optional per-sensor feature contributions in metres (XGBoost TreeSHAP).",
    )
