"""Verify an audit file from the command line.

    python -m sentinel.audit.verify data/audit/chain.jsonl
    python -m sentinel.audit.verify data/audit/chain.jsonl --anchor-seq 105119 --anchor-hash <hex>
    python -m sentinel.audit.verify data/audit/chain.jsonl --alert alert.json

``--alert`` takes the JSON of ``GET /v1/alerts/{id}`` and checks it against its record.
Prints a JSON summary. Exit code: 0 valid, 1 broken chain or alert mismatch.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sentinel.audit.chain import iter_records, verify_alert, verify_file
from sentinel.schemas.alerts import AuditRef


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a CivicWater Sentinel audit chain.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--anchor-seq", type=int)
    parser.add_argument("--anchor-hash")
    parser.add_argument("--alert", type=Path, help="alert JSON to check against its record")
    args = parser.parse_args(argv)
    if (args.anchor_seq is None) != (args.anchor_hash is None):
        parser.error("--anchor-seq and --anchor-hash go together")

    anchor = (
        AuditRef(seq=args.anchor_seq, hash=args.anchor_hash)
        if args.anchor_seq is not None
        else None
    )
    check = verify_file(args.path, anchor)
    report: dict[str, object] = {
        "path": str(args.path),
        "ok": check.ok,
        "records": check.records,
        "head": check.head.model_dump() if check.head else None,
        "broken_at": check.broken_at,
        "reason": check.reason,
    }
    ok = check.ok
    if args.alert is not None:
        alert = json.loads(args.alert.read_text(encoding="utf-8"))
        seq = (alert.get("audit") or {}).get("seq")
        record = next((r for r in iter_records(args.path) if r.get("seq") == seq), None)
        problem = verify_alert(alert, record) if record is not None else f"no record with seq {seq}"
        report["alert"] = {"alert_id": alert.get("alert_id"), "ok": problem is None}
        report["alert_problem"] = problem
        ok = ok and problem is None
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
