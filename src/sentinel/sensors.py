"""Sensor registry: the set of sensor IDs a window must contain.

Two sources are accepted (``SENTINEL_SENSOR_REGISTRY``):

- ``.csv`` — the sensor table published by the data owner (``data/vN/sensors.csv``:
  ``sensor_id,tipo,unidade,...``). This is the official BattLeDIM list; the backend reads it
  as-is so the IDs live in one place only.
- ``.json`` — ``config/sensors.provisional.json``, placeholder IDs that only respect the counts
  of the design document (33 pressure, 3 flow, 1 tank level). Development/tests only.
"""

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sentinel.schemas.windows import SensorId

# ``tipo`` column of the data owner's sensors.csv -> registry group.
CSV_KINDS = {"pressao": "pressure", "vazao": "flow", "nivel": "tank_level"}


class SensorRegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provisional: bool
    note: str = ""
    pressure: list[SensorId] = Field(min_length=1)
    flow: list[SensorId] = Field(default_factory=list)
    tank_level: list[SensorId] = Field(default_factory=list)
    units: dict[str, str] | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> "SensorRegistryFile":
        ids = [*self.pressure, *self.flow, *self.tank_level]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate sensor ids: {duplicates}")
        return self


def _read_csv(path: Path) -> SensorRegistryFile:
    groups: dict[str, list[str]] = {k: [] for k in CSV_KINDS.values()}
    units: dict[str, set[str]] = {k: set() for k in CSV_KINDS.values()}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            kind = CSV_KINDS.get(row["tipo"])
            if kind is None:
                raise ValueError(f"{path}: unknown sensor type {row['tipo']!r}")
            groups[kind].append(row["sensor_id"])
            units[kind].add(row["unidade"])
    mixed = {k: sorted(u) for k, u in units.items() if len(u) > 1}
    if mixed:
        raise ValueError(f"{path}: more than one unit per sensor type: {mixed}")
    return SensorRegistryFile(
        provisional=False,
        pressure=groups["pressure"],
        flow=groups["flow"],
        tank_level=groups["tank_level"],
        units={k: u.pop() for k, u in units.items() if u},
    )


@dataclass(frozen=True, slots=True)
class SensorRegistry:
    pressure: tuple[str, ...]
    flow: tuple[str, ...]
    tank_level: tuple[str, ...]
    provisional: bool
    source: str
    units: dict[str, str] | None = None

    @classmethod
    def load(cls, path: Path) -> "SensorRegistry":
        if path.suffix == ".csv":
            data = _read_csv(path)
        else:
            data = SensorRegistryFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        return cls(
            pressure=tuple(data.pressure),
            flow=tuple(data.flow),
            tank_level=tuple(data.tank_level),
            provisional=data.provisional,
            source=str(path),
            units=data.units,
        )

    @property
    def counts(self) -> dict[str, int]:
        return {
            "pressure": len(self.pressure),
            "flow": len(self.flow),
            "tank_level": len(self.tank_level),
        }
