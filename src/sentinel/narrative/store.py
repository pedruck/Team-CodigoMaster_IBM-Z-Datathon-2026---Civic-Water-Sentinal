"""Explanations generated in the background, stored and audited. Narrative owner (P5).

Why: Granite takes ~30 s per explanation on the VM (``docs/audit-and-narrative.md``), so it
cannot run when the operator opens an alert, and the text the operator saw must be on record.

- ``ExplanationStore``: append-only, hash-chained log of every explanation produced
  (``<run>.explanations.jsonl`` next to the audit log). Same record format as the audit chain
  (``type = "explanation.generated"``), so ``python -m sentinel.audit.verify`` checks it; the
  payload carries the ``Explanation``, ``text_sha256`` and ``alert_payload_sha256``, which
  ties it to the alert's ``alert.raised`` record (``verify_explanation``).
- ``ExplanationService``: one background worker. ``submit(alert)`` queues it; ``status()``
  answers ``pending`` (with the deterministic text meanwhile) or ``ready`` (the stored one).

Rules (one explanation per alert version and language):
- the model is called at most once when it answers: an accepted text and a rejected text are
  both final (with ``temperature=0`` a new call would give the same text);
- when the model fails (error or empty answer) the template is stored and shown, and a later
  ``submit`` tries the model again, up to ``max_attempts`` calls in total;
- every stored explanation stays in the log; the latest one for a key is the current one.
Never called inside the window pipeline: the pipeline keeps its order and latency.
"""

import logging
import queue
import threading
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentinel.audit.chain import (
    ChainVerification,
    HashChainAuditSink,
    alert_payload_sha256,
    audit_path_for,
    sha256_hex,
)
from sentinel.narrative.explain import Explanation, Narrator, explain
from sentinel.narrative.facts import DEFAULT_LANGUAGE, Language, render_text
from sentinel.schemas.alerts import Alert, AuditRef
from sentinel.schemas.api import ExplanationStatus  # contract model; still importable here

log = logging.getLogger("sentinel.narrative")

RECORD_TYPE = "explanation.generated"
_ERRORS = ("narrator_error", "narrator_empty")

Key = tuple[str, str, Language]  # alert_id, alert_payload_sha256, language


def explanations_path_for(run_id: str, root: Path | str | None = None) -> Path:
    """``~/shared/civicwater/audit/<run_id>.explanations.jsonl``, next to the audit log."""
    return audit_path_for(f"{run_id}.explanations", root)


def _is_error(fallback_reason: str | None) -> bool:
    return fallback_reason is not None and fallback_reason.startswith(_ERRORS)


class ExplanationStore:
    """Hash-chained log of explanations. ``path=None`` keeps it in memory (tests)."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        fsync: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._chain = HashChainAuditSink(path, fsync=fsync, clock=clock)

    @property
    def path(self) -> Path | None:
        return self._chain.path

    @property
    def head(self) -> AuditRef | None:
        return self._chain.head

    def append(self, explanation: Explanation, window_end: datetime, attempt: int) -> AuditRef:
        payload: dict[str, Any] = explanation.model_dump(mode="json")
        payload["text_sha256"] = sha256_hex(explanation.text.encode("utf-8"))
        payload["attempt"] = attempt
        return self._chain.append_entry(RECORD_TYPE, window_end, explanation.alert_id, payload)

    def records(self) -> list[dict[str, Any]]:
        return self._chain.records()

    def verify(self, anchor: AuditRef | None = None) -> ChainVerification:
        return self._chain.verify(anchor)


class _Entry:
    __slots__ = ("attempts", "explanation", "generated_at", "record")

    def __init__(
        self, explanation: Explanation, record: AuditRef, generated_at: datetime, attempts: int
    ) -> None:
        self.explanation = explanation
        self.record = record
        self.generated_at = generated_at
        self.attempts = attempts


class ExplanationService:
    """Background generation + store. Thread-safe; one worker, so the model runs one
    explanation at a time."""

    def __init__(
        self,
        narrator: Narrator | None,
        store: ExplanationStore,
        *,
        languages: Sequence[Language] = (DEFAULT_LANGUAGE,),
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._narrator = narrator
        self._store = store
        self._languages = tuple(languages)
        self._max_attempts = max_attempts
        self._lock = threading.Lock()
        self._entries: dict[Key, _Entry] = {}
        # Key of the most recently stored explanation per (alert_id, language): what status()
        # returns for an alert known only from the log (e.g. after a restart).
        self._latest: dict[tuple[str, Language], Key] = {}
        self._alerts: dict[str, Alert] = {}
        self._pending: set[Key] = set()
        self._queue: queue.Queue[tuple[Alert, Language] | None] = queue.Queue()
        self._load(store.records())
        self._worker = threading.Thread(target=self._run, name="explanations", daemon=True)
        self._worker.start()

    @property
    def store(self) -> ExplanationStore:
        return self._store

    @property
    def narrator_name(self) -> str | None:
        return self._narrator.name if self._narrator else None

    def submit(self, alert: Alert) -> None:
        """Queue the explanation of ``alert`` in every configured language (no-op for what is
        already final). Returns at once."""
        digest = alert_payload_sha256(alert)
        with self._lock:
            self._alerts[alert.alert_id] = alert
            for language in self._languages:
                key = (alert.alert_id, digest, language)
                if key in self._pending or not self._needs_generation(key):
                    continue
                self._pending.add(key)
                self._queue.put((alert, language))

    def status(
        self, alert_id: str, language: Language = DEFAULT_LANGUAGE
    ) -> ExplanationStatus | None:
        """Current explanation of a submitted (or previously stored) alert, else None."""
        with self._lock:
            alert = self._alerts.get(alert_id)
            entry: _Entry | None
            if alert is not None:
                key: Key = (alert_id, alert_payload_sha256(alert), language)
                entry = None if key in self._pending else self._entries.get(key)
            else:  # known only from the log (e.g. after a restart): the latest version
                latest = self._latest.get((alert_id, language))
                entry = self._entries.get(latest) if latest else None
            if entry is not None:
                e = entry.explanation
                return ExplanationStatus(
                    status="ready",
                    alert_id=alert_id,
                    language=language,
                    alert_payload_sha256=e.alert_payload_sha256,
                    source=e.source,
                    text=e.text,
                    generator=e.generator,
                    generated_at=entry.generated_at,
                    fallback_reason=e.fallback_reason,
                    record=entry.record,
                )
            if alert is None:
                return None
            return ExplanationStatus(
                status="pending",
                alert_id=alert_id,
                language=language,
                alert_payload_sha256=alert_payload_sha256(alert),
                source="template",
                text=render_text(alert, language),
                generator=None,
                generated_at=None,
                fallback_reason=None,
                record=None,
            )

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until the queue is empty (tests, notebooks). Returns False on timeout."""
        done = threading.Event()

        def join() -> None:
            self._queue.join()
            done.set()

        threading.Thread(target=join, daemon=True).start()
        return done.wait(timeout)

    def close(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=5)

    # ----------------------------------------------------------------- internals

    def _needs_generation(self, key: Key) -> bool:
        entry = self._entries.get(key)
        if entry is None:
            return True
        return _is_error(entry.explanation.fallback_reason) and (
            entry.attempts < self._max_attempts
        )

    def _load(self, records: Iterable[dict[str, Any]]) -> None:
        for record in records:
            if record.get("type") != RECORD_TYPE:
                continue
            payload = {
                k: v for k, v in record["payload"].items() if k not in ("text_sha256", "attempt")
            }
            explanation = Explanation.model_validate(payload)
            key: Key = (
                explanation.alert_id,
                explanation.alert_payload_sha256,
                explanation.language,
            )
            self._entries[key] = _Entry(
                explanation,
                AuditRef(seq=record["seq"], hash=record["hash"]),
                datetime.fromisoformat(record["recorded_at"]),
                int(record["payload"].get("attempt", 1)),
            )
            self._latest[(explanation.alert_id, explanation.language)] = key

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._generate(*job)
            except Exception:  # one bad alert never stops the worker
                log.exception("explanation failed")
            finally:
                if job is not None:
                    with self._lock:
                        alert, language = job
                        self._pending.discard(
                            (alert.alert_id, alert_payload_sha256(alert), language)
                        )
                self._queue.task_done()

    def _generate(self, alert: Alert, language: Language) -> None:
        key: Key = (alert.alert_id, alert_payload_sha256(alert), language)
        with self._lock:
            if not self._needs_generation(key):
                return
            previous = self._entries.get(key)
            attempt = previous.attempts + 1 if previous else 1
        explanation = explain(alert, self._narrator, language=language)  # slow: no lock held
        record = self._store.append(explanation, alert.window_end, attempt)
        with self._lock:
            self._entries[key] = _Entry(explanation, record, datetime.now(UTC), attempt)
            self._latest[(alert.alert_id, language)] = key
