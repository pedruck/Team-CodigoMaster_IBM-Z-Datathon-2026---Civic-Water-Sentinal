"""Semantic validation of a single window (beyond the Pydantic shape checks).

A valid window is not a valid time series: ordering, duplicates and gaps are decided by
the pipeline against the history (ADR-004).
"""

from datetime import UTC

from sentinel.schemas.windows import SensorWindow, SensorWindowIn
from sentinel.sensors import SensorRegistry
from sentinel.services.errors import WindowRejectedError


def _diff(kind: str, got: dict[str, float], expected: tuple[str, ...]) -> dict[str, list[str]]:
    missing = sorted(set(expected) - got.keys())
    unexpected = sorted(got.keys() - set(expected))
    out: dict[str, list[str]] = {}
    if missing:
        out[f"missing_{kind}"] = missing
    if unexpected:
        out[f"unexpected_{kind}"] = unexpected
    return out


def _out_of_range(window: SensorWindow, registry: SensorRegistry) -> dict[str, float]:
    if not registry.limits:
        return {}
    bad: dict[str, float] = {}
    for kind, readings in (
        ("pressure", window.pressures),
        ("flow", window.flows),
        ("tank_level", window.tank_levels),
    ):
        limit = registry.limits.get(kind)
        if limit is None:
            continue
        low, high = limit
        bad.update({sensor: v for sensor, v in readings.items() if not low <= v <= high})
    return bad


def normalize_window(window: SensorWindowIn, naive_tz: str) -> tuple[SensorWindow, bool]:
    """Return the window with a UTC timestamp and whether its timezone was assumed.

    ``naive_tz`` is ``"UTC"`` (a timestamp without timezone is read as UTC) or ``"reject"``.
    """
    if isinstance(window, SensorWindow):
        return window, False
    ts = window.timestamp
    assumed = ts.tzinfo is None
    if assumed:
        if naive_tz == "reject":
            raise WindowRejectedError(
                "timestamp_without_timezone",
                "timestamp has no timezone and SENTINEL_NAIVE_TIMESTAMP_TZ=reject",
                {"timestamp": ts.isoformat()},
            )
        ts = ts.replace(tzinfo=UTC)
    fields = {name: getattr(window, name) for name in type(window).model_fields}
    return SensorWindow.model_validate({**fields, "timestamp": ts}), assumed


def validate_window(
    window: SensorWindow,
    registry: SensorRegistry,
    interval_seconds: int,
    require_aligned: bool,
) -> None:
    """Raise ``WindowRejectedError`` if the window does not match the registry or the time grid."""
    problems = {
        **_diff("pressures", window.pressures, registry.pressure),
        **_diff("flows", window.flows, registry.flow),
        **_diff("tank_levels", window.tank_levels, registry.tank_level),
    }
    if problems:
        raise WindowRejectedError(
            "sensor_set_mismatch",
            "window sensors do not match the sensor registry",
            problems,
        )
    out_of_range = _out_of_range(window, registry)
    if out_of_range:
        raise WindowRejectedError(
            "reading_out_of_range",
            "readings outside the physical range of their sensor kind (sensor registry limits)",
            {"readings": out_of_range, "limits": registry.limits},
        )
    ts = window.timestamp
    if require_aligned and (ts.microsecond or int(ts.timestamp()) % interval_seconds):
        raise WindowRejectedError(
            "timestamp_misaligned",
            f"timestamp must be a multiple of {interval_seconds}s (UTC)",
            {"timestamp": ts.isoformat()},
        )
