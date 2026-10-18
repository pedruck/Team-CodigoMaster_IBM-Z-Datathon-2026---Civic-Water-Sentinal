"""Replay a JSONL file of windows (contract sentinel.window v1.0) against the API, in file
order, and report client-side latency. Stdlib only, so it runs wherever Python runs.

``--url`` is required: inside the VM container ``127.0.0.1:8000`` is the shared integration
API, and a test sent there by default would contaminate it. The bearer token comes from
``--token-file`` or ``SENTINEL_INGEST_TOKEN`` (needed when the API sets one).

If ``<file>.manifest.json`` exists (written by ``python -m sentinel.data.replay_windows``), the
file's SHA-256 and window count are checked against it before anything is sent.
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path


def percentile(sorted_ms: list[float], q: float) -> float:
    return sorted_ms[max(1, math.ceil(q * len(sorted_ms))) - 1]


def post(url: str, body: bytes, timeout: float, token: str | None = None) -> int:
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def check_manifest(path: Path) -> str:
    """Compare the file with its ``.manifest.json``; returns a one-line verdict, raises
    ``SystemExit`` on a mismatch."""
    sidecar = path.with_name(path.name + ".manifest.json")
    if not sidecar.exists():
        return f"no {sidecar.name}: file not checked against its manifest"
    expected = json.loads(sidecar.read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    lines = 0
    with path.open("rb") as fh:
        for line in fh:
            digest.update(line)
            lines += bool(line.strip())
    if digest.hexdigest() != expected["sha256"] or lines != expected["windows"]:
        raise SystemExit(
            f"{path} does not match {sidecar.name}: sha256 {digest.hexdigest()} / {lines} windows,"
            f" expected {expected['sha256']} / {expected['windows']}"
        )
    return f"{path.name} matches {sidecar.name} (sha256 {expected['sha256'][:12]}…)"


def read_token(token_file: Path | None) -> str | None:
    if token_file is not None:
        return token_file.read_text(encoding="utf-8").strip()
    return os.environ.get("SENTINEL_INGEST_TOKEN")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--url", required=True, help="API base URL, e.g. http://127.0.0.1:8003")
    ap.add_argument("--token-file", type=Path, help="file with the bearer token")
    ap.add_argument("--speed", type=float, default=0.0, help="windows/s; 0 = as fast as possible")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    print(check_manifest(args.file), file=sys.stderr)
    token = read_token(args.token_file)

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
            codes[post(endpoint, line.strip().encode("utf-8"), args.timeout, token)] += 1
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
