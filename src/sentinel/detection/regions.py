"""Sensor -> region mapping of the CUSUM detector (ADR-007 §8, BE-012e).

A JSON file ``{"regions": {"<region>": ["<sensor>", ...], ...}}``. The order of the regions in
the file is the tie-break order of the ranking.
"""

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from sentinel.schemas.windows import SensorId


class RegionMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    regions: dict[str, tuple[SensorId, ...]] = Field(
        min_length=1, description="Sensors of each region, regions in file (tie-break) order."
    )

    @model_validator(mode="after")
    def _each_sensor_in_one_region(self) -> "RegionMapping":
        owner: dict[str, str] = {}
        for region, members in self.regions.items():
            if not members:
                raise ValueError(f"region {region!r} has no sensors")
            for sensor in members:
                if sensor in owner:
                    raise ValueError(
                        f"sensor {sensor!r} is in both {owner[sensor]!r} and {region!r}"
                    )
                owner[sensor] = region
        return self

    _file_sha256: str | None = PrivateAttr(default=None)

    @classmethod
    def from_file(cls, path: Path) -> "RegionMapping":
        raw = path.read_bytes()
        mapping = cls.model_validate_json(raw)
        mapping._file_sha256 = hashlib.sha256(raw).hexdigest()
        return mapping

    @property
    def sensors(self) -> frozenset[str]:
        return frozenset(s for members in self.regions.values() for s in members)

    @property
    def regions_id(self) -> str:
        """sha256 of the file the mapping was loaded from; for a mapping built in memory, of
        its canonical JSON (the same rule as the calibration id)."""
        if self._file_sha256 is None:
            canonical = (self.model_dump_json(indent=2) + "\n").encode("utf-8")
            return hashlib.sha256(canonical).hexdigest()
        return self._file_sha256
