"""Small builders shared by the forecasting tests (synthetic values, real ID format)."""

from datetime import UTC, datetime, timedelta

from sentinel.forecasting.features import SensorLayout
from sentinel.schemas.forecasts import ForecastRequest
from sentinel.schemas.windows import SensorWindow

LAYOUT = SensorLayout(
    pressure=("Pressure_n1", "Pressure_n4", "Pressure_n31"),
    flow=("Flow_p227", "Flow_p235", "Flow_PUMP_1"),
    tank_level=("Level_T1",),
)
T0 = datetime(2019, 3, 14, 6, 35, tzinfo=UTC)  # Thursday


def window(ts: datetime, shift: float = 0.0) -> SensorWindow:
    return SensorWindow(
        schema_version="1.0",
        timestamp=ts,
        pressures={s: 50.0 + i + shift for i, s in enumerate(LAYOUT.pressure)},
        flows={s: 20.0 + i + shift for i, s in enumerate(LAYOUT.flow)},
        tank_levels={s: 3.0 + shift for s in LAYOUT.tank_level},
    )


def request(
    ts: datetime = T0, history: int = 1, sensors: tuple[str, ...] = LAYOUT.pressure
) -> ForecastRequest:
    past = tuple(
        window(ts - timedelta(minutes=5 * k), shift=0.01 * k) for k in range(history, 0, -1)
    )
    return ForecastRequest(
        target_timestamp=ts,
        pressure_sensor_ids=sensors,
        history=past,
        current=window(ts),
    )
