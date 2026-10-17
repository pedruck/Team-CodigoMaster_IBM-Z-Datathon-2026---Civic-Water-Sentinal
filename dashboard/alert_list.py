"""Rows of the 'Recent alerts' table: alert fields as received, age on the simulated clock."""

from datetime import datetime

from sentinel.schemas.alerts import Alert
from sentinel.schemas.detections import DeviatingSensor

NOT_PROVIDED = "not provided"
NO_REGION = "region not available"
NO_SENSOR = "no deviating sensor"


def fmt_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else NOT_PROVIDED


def fmt_age(window_end: datetime, now: datetime | None) -> str:
    """Simulated age = last accepted window minus alert window (never the wall clock)."""
    if now is None:
        return "unknown"
    minutes = int((now - window_end).total_seconds() // 60)
    if minutes < 0:
        return "after the last window"
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    return f"{hours} h {mins} min" if hours else f"{mins} min"


def _z(sensor: DeviatingSensor | None) -> str:
    if sensor is None:
        return NO_SENSOR
    return NOT_PROVIDED if sensor.z is None else f"{sensor.z:.2f}"


def alert_row(alert: Alert, now: datetime | None) -> dict[str, object]:
    strongest = max(alert.deviating_sensors, key=lambda s: abs(s.deviation_m), default=None)
    return {
        "Alert": alert.alert_id,
        "Window end": fmt_time(alert.window_end),
        "Region": alert.region or NO_REGION,
        "Sensors": len(alert.deviating_sensors),
        "Strongest deviation": (
            f"{strongest.sensor} {strongest.deviation_m:.2f} m" if strongest else NO_SENSOR
        ),
        "z": _z(strongest),
        "Estimated onset": fmt_time(alert.suspected_onset),
        "Age (simulated)": fmt_age(alert.window_end, now),
        "Mock": "SIMULATED" if alert.is_mock else "",
    }
