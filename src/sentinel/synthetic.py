"""Small synthetic window generator for tests, demos and benchmarks.

The signal (daily sinusoid + optional Gaussian noise + optional step drop) is invented and
has no relation to BattLeDIM. Never use it to claim detection performance.
"""

import math
import random
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from typing import Any

from sentinel.sensors import SensorRegistry


def window_payload(
    ts: datetime,
    registry: SensorRegistry,
    *,
    base_m: float = 50.0,
    noise_m: float = 0.0,
    drop_m: float = 0.0,
    drop_sensors: Sequence[str] = (),
    rng: random.Random | None = None,
    source: str | None = "synthetic",
) -> dict[str, Any]:
    rng = rng or random.Random(0)
    phase = 2 * math.pi * (ts.hour * 60 + ts.minute) / 1440

    def value(offset: float) -> float:
        return round(offset + math.sin(phase) + (rng.gauss(0, noise_m) if noise_m else 0.0), 4)

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "pressures": {
            s: value(base_m - (drop_m if s in drop_sensors else 0.0)) for s in registry.pressure
        },
        "flows": {s: value(20.0) for s in registry.flow},
        "tank_levels": {s: value(3.0) for s in registry.tank_level},
    }
    if source:
        payload["source"] = source
    return payload


def generate(
    start: datetime,
    count: int,
    registry: SensorRegistry,
    *,
    interval_seconds: int = 300,
    seed: int = 0,
    noise_m: float = 0.0,
    leak_at: int | None = None,
    leak_sensors: Sequence[str] = (),
    leak_drop_m: float = 5.0,
) -> Iterator[dict[str, Any]]:
    """Yield ``count`` consecutive payloads; from index ``leak_at`` on, ``leak_sensors`` read
    ``leak_drop_m`` lower (a step, which the mock detector sees for one window only)."""
    rng = random.Random(seed)
    for i in range(count):
        leaking = leak_at is not None and i >= leak_at
        yield window_payload(
            start + timedelta(seconds=i * interval_seconds),
            registry,
            noise_m=noise_m,
            drop_m=leak_drop_m if leaking else 0.0,
            drop_sensors=leak_sensors,
            rng=rng,
        )
