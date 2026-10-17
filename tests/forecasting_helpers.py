"""Small builders shared by the forecasting tests (synthetic values, real ID format)."""

from datetime import UTC, datetime, timedelta

from sentinel.forecasting.features import SensorLayout, canonical_id
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


def short_ids(req: ForecastRequest) -> ForecastRequest:
    """Same request spelled with the official registry IDs (``n1``) instead of CSV columns."""

    def strip(window: SensorWindow) -> SensorWindow:
        return SensorWindow(
            schema_version="1.0",
            timestamp=window.timestamp,
            pressures={canonical_id(k): v for k, v in window.pressures.items()},
            flows={canonical_id(k): v for k, v in window.flows.items()},
            tank_levels={canonical_id(k): v for k, v in window.tank_levels.items()},
        )

    return ForecastRequest(
        target_timestamp=req.target_timestamp,
        pressure_sensor_ids=tuple(canonical_id(s) for s in req.pressure_sensor_ids),
        history=tuple(strip(w) for w in req.history),
        current=strip(req.current),
    )
