"""Compare an audit chain with the replay file it should have recorded.

    python -m sentinel.audit.replay_check <audit.jsonl> <windows.jsonl> \\
        [--anchor-seq N --anchor-hash H]

Every accepted window leaves a ``window.*`` record whose payload has the window's
``content_hash`` (``SensorWindow.content_hash``: SHA-256 of timestamp and readings). This tool
verifies the chain first, then recomputes that hash for every window of the file (with the
backend's own normalisation) and reports, per ``window_end``:

- ``matched``: recorded with the same content;
- ``mismatched``: recorded with **different** content (the API saw other data than the file);
- ``not_recorded``: in the file but not in the chain (a partial replay, or rejected windows);
- ``not_in_file``: in the chain but not in the file (data that did not come from this file).

Exit 0 only if the chain is intact and nothing is mismatched or foreign. A partial replay is
not an error; it is reported. Run it with the anchor (``seq``/``hash`` kept outside the VM)
to also catch a rewritten chain.
"""

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sentinel.audit.chain import iter_records, verify_file
from sentinel.schemas.alerts import AuditRef
from sentinel.schemas.windows import SensorWindowIn
from sentinel.services.ingestion import normalize_window

SAMPLE = 10


@dataclass
class ReplayCheck:
    chain_ok: bool
    chain_reason: str | None
    windows_in_file: int = 0
    recorded_windows: int = 0
    matched: int = 0
    mismatched: list[str] = field(default_factory=list)
    not_recorded: int = 0
    not_in_file: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.chain_ok and not self.mismatched and not self.not_in_file

    def summary(self) -> dict[str, Any]:
        out = asdict(self)
        out["ok"] = self.ok
        out["mismatched_count"] = len(self.mismatched)
        out["mismatched"] = self.mismatched[:SAMPLE]
        out["not_in_file_count"] = len(self.not_in_file)
        out["not_in_file"] = self.not_in_file[:SAMPLE]
        return out


def recorded_hashes(records: Iterable[Mapping[str, Any]]) -> dict[str, set[str]]:
    """``window_end`` → content hashes recorded for it (more than one only if the same run
    received different data for one timestamp, e.g. after a restart)."""
    out: dict[str, set[str]] = {}
    for r in records:
        if str(r.get("type", "")).startswith("window."):
            out.setdefault(str(r["window_end"]), set()).add(str(r["payload"]["content_hash"]))
    return out


def file_hashes(lines: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        window, _ = normalize_window(SensorWindowIn.model_validate_json(line), "UTC")
        out[window.timestamp.isoformat().replace("+00:00", "Z")] = window.content_hash()
    return out


def check(audit: Path, windows: Path, anchor: AuditRef | None = None) -> ReplayCheck:
    chain = verify_file(audit, anchor)
    result = ReplayCheck(chain_ok=chain.ok, chain_reason=chain.reason)
    recorded = recorded_hashes(iter_records(audit))
    with windows.open(encoding="utf-8") as fh:
        expected = file_hashes(fh)
    result.windows_in_file = len(expected)
    result.recorded_windows = len(recorded)
    for ts, digest in expected.items():
        seen = recorded.get(ts)
        if seen is None:
            result.not_recorded += 1
        elif seen == {digest}:
            result.matched += 1
        else:
            result.mismatched.append(ts)
    result.not_in_file = sorted(set(recorded) - set(expected))
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("audit", type=Path)
    ap.add_argument("windows", type=Path)
    ap.add_argument("--anchor-seq", type=int)
    ap.add_argument("--anchor-hash")
    args = ap.parse_args(argv)
    if (args.anchor_seq is None) != (args.anchor_hash is None):
        ap.error("--anchor-seq and --anchor-hash go together")
    anchor = (
        AuditRef(seq=args.anchor_seq, hash=args.anchor_hash)
        if args.anchor_seq is not None
        else None
    )
    result = check(args.audit, args.windows, anchor)
    print(json.dumps(result.summary(), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
