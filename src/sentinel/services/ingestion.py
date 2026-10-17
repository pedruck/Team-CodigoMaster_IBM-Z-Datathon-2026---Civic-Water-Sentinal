"""Semantic validation of a single window (beyond the Pydantic shape checks).

A valid window is not a valid time series: ordering, duplicates and gaps are decided by
the pipeline against the history (ADR-004).
"""

from sentinel.schemas.windows import SensorWindow
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
    ts = window.timestamp
    if require_aligned and (ts.microsecond or int(ts.timestamp()) % interval_seconds):
        raise WindowRejectedError(
            "timestamp_misaligned",
            f"timestamp must be a multiple of {interval_seconds}s (UTC)",
            {"timestamp": ts.isoformat()},
        )
