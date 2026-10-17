"""Run one year of the data owner's ``data/vN`` export through the backend, in process.

Reads ``sensors.csv`` and ``scada_<year>.csv.gz`` (wide CSV, one row per 5-min window) and
feeds every row as a contract v1.0 window to the pipeline with the MOCK forecaster/detector.
It checks the contract, the temporal policy and the backend cost on the real data shape; mock
alerts are not detections. This is a backend check, not the official replay (data owner).

Timestamps are sent exactly as in the CSV (no timezone); the backend reads them as UTC
(SENTINEL_NAIVE_TIMESTAMP_TZ, ADR-004). Data owner to confirm UTC / end of interval (Q-DATA-02).

Usage: python scripts/validate_v1_ingestion.py <data_dir_vN> [--year 2019] [--limit N]
"""

import argparse
import csv
import gzip
import json
import platform
import time
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from sentinel.config import Settings
from sentinel.main import build_pipeline
from sentinel.schemas.windows import SensorWindowIn
from sentinel.sensors import SensorRegistry
from sentinel.services.errors import SentinelError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--year", default="2019")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    registry = SensorRegistry.load(args.data_dir / "sensors.csv")
    group = {s: "pressures" for s in registry.pressure}
    group |= {s: "flows" for s in registry.flow}
    group |= {s: "tank_levels" for s in registry.tank_level}
    pipeline = build_pipeline(Settings(audit_sink="memory"), registry=registry)

    statuses: Counter[str] = Counter()
    tz_assumed = 0
    rejected: Counter[str] = Counter()
    alerts = 0
    parse_s = 0.0
    t0 = time.perf_counter()
    path = args.data_dir / f"scada_{args.year}.csv.gz"
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        for n, row in enumerate(reader):
            if args.limit is not None and n >= args.limit:
                break
            readings: dict[str, dict[str, float]] = {
                "pressures": {},
                "flows": {},
                "tank_levels": {},
            }
            for col, raw in zip(header[1:], row[1:], strict=True):
                readings[group[col]][col] = float(raw)
            tp = time.perf_counter()
            try:
                window = SensorWindowIn.model_validate(
                    {
                        "schema_version": "1.0",
                        "timestamp": row[0],
                        "source": f"{args.data_dir.name}-scada-{args.year}",
                        **readings,
                    }
                )
            except ValidationError as exc:
                rejected[f"schema:{exc.errors()[0]['type']}"] += 1
                continue
            finally:
                parse_s += time.perf_counter() - tp
            try:
                result = pipeline.ingest(window).result
            except SentinelError as exc:
                rejected[exc.code] += 1
                continue
            statuses[result.status] += 1
            tz_assumed += result.timestamp_tz_assumed
            alerts += len(result.alert_ids)

    read = sum(statuses.values()) + sum(rejected.values())
    report = {
        "machine": platform.machine(),
        "python": platform.python_version(),
        "file": str(path),
        "windows_read": read,
        "accepted_by_status": dict(statuses),
        "rejected": dict(rejected),
        "timestamp_tz_assumed": tz_assumed,
        "mock_alerts_not_detections": alerts,
        "history": pipeline.history_info().model_dump(),
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "pydantic_parse_ms_mean": round(parse_s / max(read, 1) * 1000, 4),
        "latency_last_samples": {
            k: v.model_dump() for k, v in pipeline.metrics.latency().items() if v.count
        },
    }
    print(json.dumps(report, indent=1))
    return 0 if not rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
