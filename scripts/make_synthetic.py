"""Write synthetic windows (placeholder sensor IDs, invented signal) as JSONL."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from sentinel.config import DEFAULT_REGISTRY_PATH
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import generate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", type=Path)
    ap.add_argument("--count", type=int, default=288)
    ap.add_argument("--start", default="2019-01-01T00:00:00+00:00")
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--noise-m", type=float, default=0.05)
    ap.add_argument("--leak-at", type=int, default=None, help="index of the step drop")
    ap.add_argument("--leak-sensors", type=int, default=3, help="first N pressure sensors")
    args = ap.parse_args(argv)

    registry = SensorRegistry.load(args.registry)
    start = datetime.fromisoformat(args.start).astimezone(UTC)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for w in generate(
            start,
            args.count,
            registry,
            seed=args.seed,
            noise_m=args.noise_m,
            leak_at=args.leak_at,
            leak_sensors=registry.pressure[: args.leak_sensors],
        ):
            fh.write(json.dumps(w) + "\n")
    print(f"wrote {args.count} windows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
