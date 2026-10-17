"""Input contract: one 5-minute SCADA window (``sentinel.window`` v1.0)."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from sentinel.schemas.common import ContractModel, FiniteFloat, UtcDatetime

WINDOW_SCHEMA_VERSION: Literal["1.0"] = "1.0"

SensorId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$"),
]

Readings = Annotated[dict[SensorId, FiniteFloat], Field(min_length=1)]


class SensorWindowIn(ContractModel):
    """Window as received by ``POST /v1/windows``.

    ``timestamp`` is the **end** of the aggregation interval: each value is the mean of the
    previous ``interval_seconds`` (design document §2, Table 1). It may come without timezone
    (as in the data owner's CSV export); ingestion then applies
    ``SENTINEL_NAIVE_TIMESTAMP_TZ`` and produces a ``SensorWindow`` (ADR-004).
    """

    schema_version: Literal["1.0"]
    timestamp: datetime
    pressures: Readings
    flows: Annotated[dict[SensorId, FiniteFloat], Field(default_factory=dict)]
    tank_levels: Annotated[dict[SensorId, FiniteFloat], Field(default_factory=dict)]
    source: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = Field(
        default=None,
        description="Optional producer / replay-run identifier. Not part of the content hash.",
    )


class SensorWindow(SensorWindowIn):
    """Normalised window used inside the backend: timestamp always timezone-aware, in UTC.

    This is what forecasters and detectors receive.
    """

    timestamp: UtcDatetime

    def content_hash(self) -> str:
        """SHA-256 of the measurements, used to tell an idempotent resend from a conflict."""
        canonical = json.dumps(
            {
                "timestamp": self.timestamp.isoformat(),
                "pressures": self.pressures,
                "flows": self.flows,
                "tank_levels": self.tank_levels,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
