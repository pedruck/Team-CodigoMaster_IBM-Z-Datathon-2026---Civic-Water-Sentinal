"""Append-only, hash-chained audit log (design document §6, contracts §4). Audit owner (P5).

Every event handed over by the pipeline becomes one record (one JSON line in the file)::

    {"record_version": "1.0", "seq": 0, "recorded_at": "...Z", "type": "window.scored",
     "window_end": "...Z", "alert_id": null, "payload": {...},
     "payload_sha256": "<hex>", "prev_hash": "<hex>", "hash": "<hex>"}

    payload_sha256 = SHA-256(canonical_json(payload))
    hash           = SHA-256(prev_hash || canonical_json(record without prev_hash and hash))

``prev_hash`` of the first record is ``GENESIS_HASH``. Changing, removing, inserting or
reordering a record breaks the chain from that record on, and ``verify_*`` reports where.

Limitation: someone able to rewrite the whole file can recompute a consistent chain. Keep a
copy of the head (``seq`` + ``hash`` of the last record) outside the VM and pass it as
``anchor`` when verifying.
"""

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentinel.audit.sinks import AuditError
from sentinel.schemas.alerts import Alert, AuditEvent, AuditRef

RECORD_VERSION = "1.0"
GENESIS_HASH = "0" * 64
RECORD_FIELDS = frozenset(
    {
        "record_version",
        "seq",
        "recorded_at",
        "type",
        "window_end",
        "alert_id",
        "payload",
        "payload_sha256",
        "prev_hash",
        "hash",
    }
)


def canonical_json(value: Any) -> bytes:
    """Sorted keys, no whitespace, ASCII escapes, NaN/Infinity rejected (same rules as
    ``SensorWindow.content_hash``). Raises ``TypeError``/``ValueError`` on non-JSON values."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_hash(record: Mapping[str, Any]) -> str:
    """Hash of a record given its ``prev_hash``; ``hash`` itself is ignored."""
    body = {k: v for k, v in record.items() if k not in ("prev_hash", "hash")}
    return sha256_hex(str(record["prev_hash"]).encode("ascii") + canonical_json(body))


def alert_payload_sha256(alert: Alert | Mapping[str, Any]) -> str:
    """Hash of an alert exactly as the pipeline audits it: without its ``audit`` field
    (Q-AUD-05). Accepts the model or the JSON returned by ``GET /v1/alerts/{id}``."""
    if isinstance(alert, Alert):
        payload: Mapping[str, Any] = alert.model_dump(mode="json", exclude={"audit"})
    else:
        payload = {k: v for k, v in alert.items() if k != "audit"}
    return sha256_hex(canonical_json(payload))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def audit_path_for(run_id: str, root: Path | str | None = None) -> Path:
    """Agreed location of a run's log on the VM (Q-AUD-02):
    ``~/shared/civicwater/audit/<run_id>.jsonl``, outside every git clone."""
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError(f"run_id must match {_RUN_ID.pattern}, got {run_id!r}")
    base = Path(root) if root is not None else Path.home() / "shared" / "civicwater" / "audit"
    return base / f"{run_id}.jsonl"


# ----------------------------------------------------------------------------- verification


@dataclass(frozen=True, slots=True)
class ChainVerification:
    ok: bool
    records: int = 0  # valid records before the first problem
    head: AuditRef | None = None
    broken_at: int | None = None  # 0-based line / seq of the first bad record
    reason: str | None = None


def _content_problem(record: Mapping[str, Any]) -> str | None:
    try:
        if record["payload_sha256"] != sha256_hex(canonical_json(record["payload"])):
            return "payload_sha256 does not match the payload (payload altered)"
        if record["hash"] != record_hash(record):
            return "hash does not match the record content (record altered)"
    except (TypeError, ValueError) as exc:
        return f"record is not canonical JSON: {exc}"
    return None


class _Cursor:
    """Checks records one at a time, so a year-long file is verified without loading it."""

    def __init__(self, anchor: AuditRef | None) -> None:
        self.prev_hash = GENESIS_HASH
        self.count = 0
        self.anchor = anchor
        self.anchor_ok = anchor is None

    def check(self, record: Any) -> str | None:
        """Return the problem with ``record``, or advance past it and return None."""
        problem = self._problem(record)
        if problem is None:
            self.anchor_ok |= self.anchor is not None and self.count == self.anchor.seq
            self.prev_hash = record["hash"]
            self.count += 1
        return problem

    def _problem(self, record: Any) -> str | None:
        if not isinstance(record, dict):
            return "record is not a JSON object"
        problem: str | None = None
        if set(record) != RECORD_FIELDS:
            missing = sorted(RECORD_FIELDS - set(record))
            extra = sorted(set(record) - RECORD_FIELDS)
            problem = f"unexpected fields: missing={missing} extra={extra}"
        elif record["seq"] != self.count:
            problem = f"sequence break: expected seq {self.count}, found {record['seq']!r}"
        elif record["prev_hash"] != self.prev_hash:
            problem = "prev_hash does not match the previous record (removed or reordered record)"
        else:
            problem = _content_problem(record)
        anchor = self.anchor
        if (
            problem is None
            and anchor
            and anchor.seq == self.count
            and anchor.hash != record["hash"]
        ):
            problem = f"hash differs from the external anchor for seq {anchor.seq}"
        return problem

    def result(self, broken_at: int | None = None, reason: str | None = None) -> ChainVerification:
        head = AuditRef(seq=self.count - 1, hash=self.prev_hash) if self.count else None
        if reason is None and not self.anchor_ok:
            assert self.anchor is not None
            broken_at = self.count
            reason = f"anchor seq {self.anchor.seq} not found: the log was truncated"
        return ChainVerification(
            ok=reason is None, records=self.count, head=head, broken_at=broken_at, reason=reason
        )


def verify_records(
    records: Iterable[Mapping[str, Any]], anchor: AuditRef | None = None
) -> ChainVerification:
    """Verify an in-memory sequence of records. ``anchor``: a head kept elsewhere; the
    record with ``anchor.seq`` must still exist and still have ``anchor.hash``."""
    cursor = _Cursor(anchor)
    for i, record in enumerate(records):
        reason = cursor.check(record)
        if reason:
            return cursor.result(i, reason)
    return cursor.result()


def verify_file(path: Path | str, anchor: AuditRef | None = None) -> ChainVerification:
    """Verify a JSON-lines audit file, streaming it."""
    cursor = _Cursor(anchor)
    with Path(path).open("rb") as f:
        for i, line in enumerate(f):
            try:
                record = json.loads(line)
            except ValueError:
                return cursor.result(i, "line is not valid JSON (partial or edited write)")
            reason = cursor.check(record)
            if reason:
                return cursor.result(i, reason)
    return cursor.result()


def iter_records(path: Path | str) -> Iterator[dict[str, Any]]:
    """Yield the records of an audit file. Does not verify them: use ``verify_file``."""
    with Path(path).open("rb") as f:
        for line in f:
            yield json.loads(line)


def verify_alert(alert: Alert | Mapping[str, Any], record: Mapping[str, Any]) -> str | None:
    """Check that ``record`` is the audit record of ``alert``. Returns the problem, or None.

    Only links the alert to the record: verify the chain itself with ``verify_*``."""
    alert_id: Any
    if isinstance(alert, Alert):
        alert_id, ref = alert.alert_id, alert.audit
    else:
        alert_id = alert.get("alert_id")
        raw = alert.get("audit")
        ref = AuditRef.model_validate(raw) if raw is not None else None
    if ref is None:
        return "alert has no audit reference (audit disabled or failed)"
    if record.get("seq") != ref.seq or record.get("hash") != ref.hash:
        return f"record seq/hash differ from the alert's audit reference {ref.seq}"
    if record.get("type") != "alert.raised" or record.get("alert_id") != alert_id:
        return f"record {ref.seq} is not the alert.raised record of {alert_id}"
    if record.get("payload_sha256") != alert_payload_sha256(alert):
        return "alert content differs from the audited payload (alert altered)"
    return None


def verify_explanation(
    explanation_record: Mapping[str, Any], alert_record: Mapping[str, Any]
) -> str | None:
    """Check that an ``explanation.generated`` record explains the alert of ``alert_record``
    (its ``alert.raised`` record in the audit chain), and that the stored text is the one
    whose hash was recorded. Returns the problem, or None."""
    if explanation_record.get("type") != "explanation.generated":
        return "not an explanation.generated record"
    payload = explanation_record.get("payload") or {}
    if alert_record.get("type") != "alert.raised":
        return "the second record is not an alert.raised record"
    if payload.get("alert_id") != alert_record.get("alert_id"):
        return "explanation and alert record have different alert_id"
    if payload.get("alert_payload_sha256") != alert_record.get("payload_sha256"):
        return "explanation was made for another version of the alert"
    if payload.get("text_sha256") != sha256_hex(str(payload.get("text", "")).encode("utf-8")):
        return "explanation text differs from its recorded hash"
    return None


# ----------------------------------------------------------------------------------- sink


class HashChainAuditSink:
    """``AuditSink`` with SHA-256 chaining.

    ``path=None`` keeps the records in memory (tests, notebooks). With a path, each record is
    appended as one JSON line and flushed (+ fsync) before ``append`` returns; an existing
    file is verified on start-up and extended, so ``seq`` continues across restarts. A
    broken file is never extended: start-up raises ``AuditError``.

    The backend is fail-closed (contract §4, ADR-006): an event whose ``append`` raised is
    retried later, so ``AuditError`` must mean "not stored". If the file no longer has the
    size this sink left it with (another process wrote to it, or a failed write could not
    be undone), ``append`` refuses with ``AuditError`` instead of writing a second record
    with the same ``seq``. The pipeline then holds the alerts and the problem becomes
    visible; restarting re-verifies the file.
    """

    name = "hash-chain"
    enabled = True

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        fsync: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._fsync = fsync
        self._clock = clock
        self._lock = threading.Lock()
        self._memory: list[dict[str, Any]] | None = [] if self._path is None else None
        self._next_seq = 0
        self._prev_hash = GENESIS_HASH
        self._size = 0  # bytes this sink expects in the file
        if self._path is not None and self._path.exists():
            check = verify_file(self._path)
            if not check.ok:
                raise AuditError(
                    f"refusing to extend a broken audit chain {self._path}: "
                    f"record {check.broken_at}: {check.reason}"
                )
            if check.head is not None and check.head.hash is not None:
                self._next_seq = check.head.seq + 1
                self._prev_hash = check.head.hash
            self._size = self._path.stat().st_size

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def head(self) -> AuditRef | None:
        """``seq``/``hash`` of the last record: the value to keep outside the VM."""
        with self._lock:
            if self._next_seq == 0:
                return None
            return AuditRef(seq=self._next_seq - 1, hash=self._prev_hash)

    def append(self, event: AuditEvent) -> AuditRef:
        return self.append_entry(event.type, event.window_end, event.alert_id, event.payload)

    def append_entry(
        self,
        type_: str,
        window_end: datetime,
        alert_id: str | None,
        payload: Mapping[str, Any],
    ) -> AuditRef:
        """Append a record of any ``type``. The pipeline's events go through ``append``; P5's
        own logs (e.g. ``explanation.generated``) use this with the same record format, so
        ``verify_file`` and the CLI verify them too."""
        with self._lock:
            try:
                record = self._build(type_, window_end, alert_id, payload)
                line = canonical_json(record) + b"\n"
            except (TypeError, ValueError) as exc:
                raise AuditError(f"event payload is not canonical JSON: {exc}") from exc
            if self._memory is not None:
                self._memory.append(record)
            else:
                self._write(line)
            # State moves only after the record is stored: a failed append changes nothing.
            self._next_seq += 1
            self._prev_hash = record["hash"]
            return AuditRef(seq=record["seq"], hash=record["hash"])

    def records(self) -> list[dict[str, Any]]:
        """Copy of every record, oldest first."""
        with self._lock:
            if self._memory is not None:
                return [json.loads(canonical_json(r)) for r in self._memory]
        assert self._path is not None
        return list(iter_records(self._path)) if self._path.exists() else []

    def get(self, seq: int) -> dict[str, Any] | None:
        return next((r for r in self.records() if r["seq"] == seq), None)

    def records_after(self, after_seq: int = -1, limit: int = 100) -> list[dict[str, Any]]:
        """Up to ``limit`` records with ``seq > after_seq``, in ``seq`` order and exactly as
        stored: the page of the read endpoint proposed in Q-AUD-06 (``GET /v1/audit``).
        Streams the file, so a year-long log is not loaded."""
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with self._lock:
            if self._memory is not None:
                rows = [r for r in self._memory if r["seq"] > after_seq][:limit]
                return [json.loads(canonical_json(r)) for r in rows]
        assert self._path is not None
        if not self._path.exists():
            return []
        page: list[dict[str, Any]] = []
        for record in iter_records(self._path):
            if record.get("seq", -1) > after_seq:
                page.append(record)
                if len(page) == limit:
                    break
        return page

    def verify(self, anchor: AuditRef | None = None) -> ChainVerification:
        if self._path is None:
            return verify_records(self.records(), anchor)
        if not self._path.exists():
            return verify_records([], anchor)
        return verify_file(self._path, anchor)

    def _build(
        self,
        type_: str,
        window_end: datetime,
        alert_id: str | None,
        event_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        # Round trip: the stored payload is exactly what was hashed (tuples become lists,
        # and later changes to the caller's dict cannot reach the log).
        payload = json.loads(canonical_json(dict(event_payload)))
        record: dict[str, Any] = {
            "record_version": RECORD_VERSION,
            "seq": self._next_seq,
            "recorded_at": _iso(self._clock()),
            "type": type_,
            "window_end": _iso(window_end),
            "alert_id": alert_id,
            "payload": payload,
            "payload_sha256": sha256_hex(canonical_json(payload)),
            "prev_hash": self._prev_hash,
        }
        record["hash"] = record_hash(record)
        return record

    def _write(self, line: bytes) -> None:
        assert self._path is not None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("ab") as f:
                start = f.tell()
                if start != self._size:
                    raise AuditError(
                        f"audit file {self._path} changed outside this sink (expected "
                        f"{self._size} bytes, found {start}); refusing to append, restart "
                        "to re-verify it"
                    )
                try:
                    f.write(line)
                    f.flush()
                    if self._fsync:
                        os.fsync(f.fileno())
                except OSError:
                    f.truncate(start)  # never leave half a record behind
                    raise
                self._size = start + len(line)
        except OSError as exc:
            raise AuditError(f"could not append to {self._path}: {exc}") from exc
