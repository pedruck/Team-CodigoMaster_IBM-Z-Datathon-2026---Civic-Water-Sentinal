"""In-process metrics: per-stage latency samples and counters (no external backend).

Percentiles are computed over the last ``sample_size`` observations of each stage
(nearest-rank on the sorted samples). Values reset on restart.
"""

import math
import threading
import time
from collections import Counter, deque

from sentinel.schemas.api import LatencySummary

STAGES = ("validation", "temporal_policy", "history", "forecast", "detection", "audit", "total")


def _percentile(sorted_values: list[float], q: float) -> float:
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[rank - 1]


class Metrics:
    def __init__(self, sample_size: int = 10_000) -> None:
        self._samples: dict[str, deque[float]] = {s: deque(maxlen=sample_size) for s in STAGES}
        self._counters: Counter[str] = Counter()
        self._lock = threading.Lock()
        self.started_monotonic = time.monotonic()

    def observe(self, stage: str, seconds: float) -> None:
        with self._lock:
            self._samples.setdefault(stage, deque(maxlen=10_000)).append(seconds * 1000.0)

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] += n

    def counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def latency(self) -> dict[str, LatencySummary]:
        with self._lock:
            snapshot = {k: sorted(v) for k, v in self._samples.items()}
        out: dict[str, LatencySummary] = {}
        for stage, values in snapshot.items():
            if not values:
                out[stage] = LatencySummary(
                    count=0, mean_ms=None, p50_ms=None, p95_ms=None, p99_ms=None, max_ms=None
                )
                continue
            out[stage] = LatencySummary(
                count=len(values),
                mean_ms=round(sum(values) / len(values), 4),
                p50_ms=round(_percentile(values, 0.50), 4),
                p95_ms=round(_percentile(values, 0.95), 4),
                p99_ms=round(_percentile(values, 0.99), 4),
                max_ms=round(values[-1], 4),
            )
        return out

    def uptime_s(self) -> float:
        return time.monotonic() - self.started_monotonic


class StageTimer:
    """Collects per-stage durations for one window; flushed to ``Metrics`` at the end."""

    def __init__(self) -> None:
        self.durations: dict[str, float] = {}
        self._t0 = time.perf_counter()

    def measure(self, stage: str, start: float) -> None:
        self.durations[stage] = time.perf_counter() - start

    def finish(self) -> None:
        self.durations["total"] = time.perf_counter() - self._t0

    def as_ms(self) -> dict[str, float]:
        return {k: round(v * 1000.0, 4) for k, v in self.durations.items()}
