"""In-process benchmark of the pipeline (no HTTP) with the MOCK forecaster/detector.

Measures backend overhead only; real model latency must be measured with the real models
on LinuxONE (see docs/performance.md).
"""

import argparse
import json
import platform
import sys
from datetime import UTC, datetime

from sentinel.config import Settings
from sentinel.main import build_pipeline
from sentinel.schemas.windows import SensorWindow
from sentinel.synthetic import generate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", type=int, default=5000)
    ap.add_argument("--audit", choices=["null", "memory"], default="memory")
    args = ap.parse_args(argv)

    settings = Settings(audit_sink=args.audit)
    pipeline = build_pipeline(settings)
    windows = [
        SensorWindow.model_validate(p)
        for p in generate(
            datetime(2019, 1, 1, tzinfo=UTC), args.windows, pipeline.registry, noise_m=0.05
        )
    ]
    for w in windows:
        pipeline.ingest(w)
    report = {
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "platform": platform.platform(),
        "windows": args.windows,
        "settings": {
            "history_capacity": settings.history_capacity,
            "min_history_windows": settings.min_history_windows,
            "audit_sink": args.audit,
        },
        "counters": pipeline.metrics.counters(),
        "latency": {k: v.model_dump() for k, v in pipeline.metrics.latency().items()},
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
