"""Bounded in-memory stores for window results (idempotency) and alerts. Lost on restart."""

import threading
from collections import OrderedDict
from datetime import datetime

from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import WindowResult


class ResultStore:
    """``timestamp -> (content_hash, WindowResult)``; evicts the oldest beyond capacity."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: OrderedDict[datetime, tuple[str, WindowResult]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, ts: datetime) -> tuple[str, WindowResult] | None:
        with self._lock:
            return self._items.get(ts)

    def put(self, ts: datetime, content_hash: str, result: WindowResult) -> None:
        with self._lock:
            self._items[ts] = (content_hash, result)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class AlertStore:
    """Alerts in insertion (chronological) order; evicts the oldest beyond capacity."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: OrderedDict[str, Alert] = OrderedDict()
        self._lock = threading.Lock()
        self.evicted = 0

    def add(self, alert: Alert) -> None:
        with self._lock:
            self._items[alert.alert_id] = alert
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)
                self.evicted += 1

    def get(self, alert_id: str) -> Alert | None:
        with self._lock:
            return self._items.get(alert_id)

    def query(
        self,
        *,
        limit: int,
        offset: int,
        since: datetime | None = None,
        until: datetime | None = None,
        region: str | None = None,
    ) -> tuple[list[Alert], int]:
        """Newest first. ``since``/``until`` filter on ``window_end`` (inclusive)."""
        with self._lock:
            items = list(reversed(self._items.values()))
        selected = [
            a
            for a in items
            if (since is None or a.window_end >= since)
            and (until is None or a.window_end <= until)
            and (region is None or a.region == region)
        ]
        return selected[offset : offset + limit], len(selected)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
