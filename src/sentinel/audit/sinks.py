"""Audit sink port. The append-only, hash-chained log (design document §6) belongs to the
audit owner; nothing here is tamper-evident. See docs/integration-contracts.md §4."""

import threading
from typing import Protocol, runtime_checkable

from sentinel.schemas.alerts import AuditEvent, AuditRef


class AuditError(Exception):
    """The event could not be durably appended."""


@runtime_checkable
class AuditSink(Protocol):
    name: str
    enabled: bool

    def append(self, event: AuditEvent) -> AuditRef:
        """Append the event and return its reference, or raise ``AuditError``."""
        ...


class NullAuditSink:
    """Audit disabled. ``append`` must not be called; the pipeline reports ``disabled``."""

    name = "null"
    enabled = False

    def append(self, event: AuditEvent) -> AuditRef:
        raise AuditError("audit sink disabled")


class InMemoryAuditSink:
    """Development/test sink: keeps events in a list. No hashing, lost on restart."""

    name = "memory"
    enabled = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> AuditRef:
        with self._lock:
            self.events.append(event)
            return AuditRef(seq=len(self.events) - 1)
