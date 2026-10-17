"""Feature definitions shared by training and inference (forecasters).

``sentinel.forecasting.training`` builds the same features vectorised with pandas and the
notebooks assert, on a sample of rows, that both match exactly (train/serve parity). Any
change here must be mirrored there and the models retrained.

Two spellings of the same sensor IDs exist in the project: the official registry
(``config/sensors.battledim.json``: ``n1``, ``p227``, ``T1``) and the data owner's CSV column
names (``data/vN/sensors.csv``: ``Pressure_n1``, ``Flow_p227``, ``Level_T1``). Models store the
spelling they were trained with; ``IdTranslator`` maps the spelling of each request onto it,
so a model works whichever registry the API is started with.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
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


COLUMN_PREFIXES = ("Pressure_", "Flow_", "Level_")


def canonical_id(sensor_id: str) -> str:
    """``Pressure_n1`` -> ``n1``; ``n1`` stays ``n1``. Network elements are unique in L-Town."""
    for prefix in COLUMN_PREFIXES:
        if sensor_id.startswith(prefix):
            return sensor_id[len(prefix) :]
    return sensor_id


class IdTranslator:
    """Maps request sensor IDs (either spelling) to the IDs a model was trained with."""

    def __init__(self, layout: SensorLayout) -> None:
        ids = (*layout.pressure, *layout.flow, *layout.tank_level)
        self._model_ids = set(ids)
        self._by_canonical = {canonical_id(s): s for s in ids}

    def to_model(self, sensor_id: str) -> str:
        if sensor_id in self._model_ids:
            return sensor_id
        return self._by_canonical.get(canonical_id(sensor_id), sensor_id)

    def mapping(self, sensor_ids: Iterable[str]) -> dict[str, str]:
        return {s: self.to_model(s) for s in sensor_ids}

    def window(self, window: SensorWindow) -> SensorWindow:
        """Same window with the readings keyed by model IDs (no copy if already aligned)."""
        keys = (*window.pressures, *window.flows, *window.tank_levels)
        if all(k in self._model_ids for k in keys):
            return window
        return window.model_copy(
            update={
                "pressures": {self.to_model(k): v for k, v in window.pressures.items()},
                "flows": {self.to_model(k): v for k, v in window.flows.items()},
                "tank_levels": {self.to_model(k): v for k, v in window.tank_levels.items()},
            }
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
