"""Run a JSONL replay (contract ``sentinel.window`` v1.0) through the backend, in process.

Input: the data owner's replay file, e.g. ``$SENTINEL_DATA_DIR/replay/v1/windows_2019.jsonl``
(``python -m sentinel.data.replay_windows``). Every line is parsed as the HTTP input model and
fed to the pipeline with the MOCK forecaster/detector and in-memory audit. It checks the
contract, the temporal policy and the backend cost without HTTP; mock alerts are not
detections. For the HTTP path use ``scripts/replay.py`` against a running API.

Usage: python scripts/validate_replay_ingestion.py <file.jsonl>
       [--registry config/sensors.battledim.json] [--limit N]
"""

import argparse
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
    ap.add_argument("file", type=Path)
    ap.add_argument("--registry", type=Path, default=Path("config/sensors.battledim.json"))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    registry = SensorRegistry.load(args.registry)
    pipeline = build_pipeline(Settings(audit_sink="memory"), registry=registry)

    statuses: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    tz_assumed = alerts = 0
    parse_s = 0.0
    t0 = time.perf_counter()
    with args.file.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh):
            if args.limit is not None and n >= args.limit:
                break
            tp = time.perf_counter()
            try:
                window = SensorWindowIn.model_validate_json(line)
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
        "file": str(args.file),
        "registry": str(args.registry),
        "windows_read": read,
        "accepted_by_status": dict(statuses),
        "rejected": dict(rejected),
        "timestamp_tz_assumed": tz_assumed,
        "mock_alerts_not_detections": alerts,
        "audit_pending": pipeline.audit_pending,
        "history": pipeline.history_info().model_dump(),
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "json_parse_ms_mean": round(parse_s / max(read, 1) * 1000, 4),
        "latency_last_samples": {
            k: v.model_dump() for k, v in pipeline.metrics.latency().items() if v.count
        },
    }
    print(json.dumps(report, indent=1))
    return 0 if not rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
