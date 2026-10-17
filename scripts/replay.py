"""Replay a JSONL file of windows (contract sentinel.window v1.0) against the API, in file
order, and report client-side latency. Stdlib only, so it runs wherever Python runs.
"""

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path


def percentile(sorted_ms: list[float], q: float) -> float:
    return sorted_ms[max(1, math.ceil(q * len(sorted_ms))) - 1]


def post(url: str, body: bytes, timeout: float) -> int:
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--speed", type=float, default=0.0, help="windows/s; 0 = as fast as possible")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    endpoint = args.url.rstrip("/") + "/v1/windows"
    codes: Counter[int] = Counter()
    latencies: list[float] = []
    t_start = time.perf_counter()
    with args.file.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh):
            if args.limit is not None and n >= args.limit:
                break
            if not line.strip():
                continue
            t = time.perf_counter()
            codes[post(endpoint, line.strip().encode("utf-8"), args.timeout)] += 1
            latencies.append((time.perf_counter() - t) * 1000)
            if args.speed > 0:
                time.sleep(max(0.0, 1 / args.speed - (time.perf_counter() - t)))
    elapsed = time.perf_counter() - t_start
    latencies.sort()
    report = {
        "windows": len(latencies),
        "status_codes": dict(sorted(codes.items())),
        "elapsed_s": round(elapsed, 3),
        "throughput_windows_per_s": round(len(latencies) / elapsed, 2) if elapsed else None,
        "client_latency_ms": {
            "p50": round(percentile(latencies, 0.5), 3),
            "p99": round(percentile(latencies, 0.99), 3),
            "max": round(latencies[-1], 3),
        }
        if latencies
        else None,
    }
    print(json.dumps(report, indent=2))
    return 0 if all(c < 500 for c in codes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
