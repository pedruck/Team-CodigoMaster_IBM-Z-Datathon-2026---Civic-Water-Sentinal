"""Sensor registry: the set of sensor IDs a window must contain.

The IDs shipped in ``config/sensors.provisional.json`` are placeholders that only respect
the counts of the design document (33 pressure, 3 flow, 1 tank level). The official
BattLeDIM IDs must come from the data owner (dependency DEP-DATA-01).
"""

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sentinel.schemas.windows import SensorId


class SensorRegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provisional: bool
    note: str = ""
    pressure: list[SensorId] = Field(min_length=1)
    flow: list[SensorId] = Field(default_factory=list)
    tank_level: list[SensorId] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> "SensorRegistryFile":
        ids = [*self.pressure, *self.flow, *self.tank_level]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate sensor ids: {duplicates}")
        return self


@dataclass(frozen=True, slots=True)
class SensorRegistry:
    pressure: tuple[str, ...]
    flow: tuple[str, ...]
    tank_level: tuple[str, ...]
    provisional: bool
    source: str

    @classmethod
    def load(cls, path: Path) -> "SensorRegistry":
        data = SensorRegistryFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        return cls(
            pressure=tuple(data.pressure),
            flow=tuple(data.flow),
            tank_level=tuple(data.tank_level),
            provisional=data.provisional,
            source=str(path),
        )

    @property
    def counts(self) -> dict[str, int]:
        return {
            "pressure": len(self.pressure),
            "flow": len(self.flow),
            "tank_level": len(self.tank_level),
        }
