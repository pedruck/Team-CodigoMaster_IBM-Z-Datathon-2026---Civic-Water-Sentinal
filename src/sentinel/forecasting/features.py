"""Feature definitions shared by training (notebooks) and inference (forecasters).

The notebooks build the same features vectorised with pandas and assert, on a sample of
rows, that they match these functions exactly (train/serve parity). Any change here must be
mirrored in ``notebooks/p2_forecast/p2lib.py`` and the models retrained.

Sensor IDs are the data owner's column names (``Pressure_n1``, ``Flow_p227``, ``Level_T1``),
the same IDs the backend reads from ``data/vN/sensors.csv``.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sentinel.schemas.windows import SensorWindow

# Contribution groups used in the alert payload (design doc §6).
GROUP_BASE = "base"
GROUP_FLOW = "inlet_flow"
GROUP_LEVEL = "tank_level"
GROUP_CALENDAR = "hour_of_week"
GROUP_SENSORS = "other_sensors"


@dataclass(frozen=True, slots=True)
class SensorLayout:
    """Ordered sensor IDs, as in the registry. The order defines the feature order."""

    pressure: tuple[str, ...]
    flow: tuple[str, ...]
    tank_level: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "pressure": list(self.pressure),
            "flow": list(self.flow),
            "tank_level": list(self.tank_level),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SensorLayout":
        return cls(
            pressure=tuple(data["pressure"]),
            flow=tuple(data.get("flow", ())),
            tank_level=tuple(data.get("tank_level", ())),
        )


def hour_of_day(ts: datetime) -> float:
    """Hour as a fraction, e.g. 06:30 -> 6.5. ``ts`` is the end of the 5-minute window."""
    return ts.hour + ts.minute / 60.0


# --- XGBoost (one model per pressure sensor) -------------------------------------------------


def xgb_feature_names(layout: SensorLayout, target: str) -> list[str]:
    """Inputs of the model that predicts ``target``: flows, tank level, calendar and the
    *other* pressure sensors at the same instant (design doc §3, Table 2). The target's own
    reading is never an input (contracts §2, no-leakage rule)."""
    if target not in layout.pressure:
        raise KeyError(f"{target} is not a pressure sensor of the layout")
    return [
        *(f"flow:{s}" for s in layout.flow),
        *(f"level:{s}" for s in layout.tank_level),
        "cal:hour",
        "cal:day_of_week",
        *(f"pressure:{s}" for s in layout.pressure if s != target),
    ]


def xgb_features(window: SensorWindow, layout: SensorLayout, target: str) -> list[float]:
    """Feature row for ``target`` from the window being scored. Same order as the names."""
    ts = window.timestamp
    return [
        *(window.flows[s] for s in layout.flow),
        *(window.tank_levels[s] for s in layout.tank_level),
        hour_of_day(ts),
        float(ts.weekday()),
        *(window.pressures[s] for s in layout.pressure if s != target),
    ]


def feature_group(name: str) -> str:
    """Contribution group of an XGBoost feature name."""
    kind = name.split(":", 1)[0]
    return {
        "flow": GROUP_FLOW,
        "level": GROUP_LEVEL,
        "cal": GROUP_CALENDAR,
        "pressure": GROUP_SENSORS,
    }[kind]


def group_contributions(
    names: Sequence[str], values: Sequence[float], bias: float
) -> dict[str, float]:
    """Sum TreeSHAP contributions per group. The groups plus ``base`` add up to the forecast."""
    if len(names) != len(values):
        raise ValueError("names and values must have the same length")
    grouped = {GROUP_BASE: float(bias)}
    for name, value in zip(names, values, strict=True):
        group = feature_group(name)
        grouped[group] = grouped.get(group, 0.0) + float(value)
    return grouped


# --- GRU (one model for all pressure sensors) ------------------------------------------------


def gru_feature_names(layout: SensorLayout) -> list[str]:
    """Per-timestep inputs: all pressures, flows, tank level and a cyclic calendar."""
    return [
        *(f"pressure:{s}" for s in layout.pressure),
        *(f"flow:{s}" for s in layout.flow),
        *(f"level:{s}" for s in layout.tank_level),
        "cal:hour_sin",
        "cal:hour_cos",
        "cal:week_sin",
        "cal:week_cos",
    ]


def gru_calendar(ts: datetime) -> list[float]:
    hour = hour_of_day(ts)
    week = (ts.weekday() + hour / 24.0) / 7.0
    return [
        math.sin(2 * math.pi * hour / 24.0),
        math.cos(2 * math.pi * hour / 24.0),
        math.sin(2 * math.pi * week),
        math.cos(2 * math.pi * week),
    ]


def gru_step_features(window: SensorWindow, layout: SensorLayout) -> list[float]:
    """One timestep of the GRU input. Same order as ``gru_feature_names``."""
    return [
        *(window.pressures[s] for s in layout.pressure),
        *(window.flows[s] for s in layout.flow),
        *(window.tank_levels[s] for s in layout.tank_level),
        *gru_calendar(window.timestamp),
    ]
