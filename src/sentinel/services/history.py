"""Bounded, in-memory history of accepted windows (ADR-003).

Lost on process restart. Thread-safe on its own; the pipeline additionally serialises the
whole ingest sequence.
"""

import threading
from collections import deque
from datetime import datetime, timedelta

from sentinel.schemas.windows import SensorWindow


class HistoryBuffer:
    def __init__(self, capacity: int, interval_seconds: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._windows: deque[SensorWindow] = deque(maxlen=capacity)
        self._interval = timedelta(seconds=interval_seconds)
        self._contiguous = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._windows.maxlen or 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._windows)

    @property
    def contiguous(self) -> int:
        """Windows at the tail of the buffer with no gap between them."""
        with self._lock:
            return self._contiguous

    @property
    def last_timestamp(self) -> datetime | None:
        with self._lock:
            return self._windows[-1].timestamp if self._windows else None

    def append(self, window: SensorWindow) -> int:
        """Append a window strictly newer than the last one; return the missing intervals."""
        with self._lock:
            gap = 0
            if self._windows:
                last = self._windows[-1].timestamp
                if window.timestamp <= last:
                    raise ValueError(
                        f"window {window.timestamp.isoformat()} is not after {last.isoformat()}"
                    )
                gap = int((window.timestamp - last) / self._interval) - 1
            self._windows.append(window)
            self._contiguous = 1 if gap > 0 else min(self._contiguous + 1, len(self._windows))
            return gap

    def contiguous_tail(
        self, n: int | None = None, *, exclude_last: bool = False
    ) -> tuple[SensorWindow, ...]:
        """Most recent contiguous windows, ascending. ``exclude_last`` drops the newest one."""
        with self._lock:
            k = self._contiguous - (1 if exclude_last else 0)
            if n is not None:
                k = min(k, n)
            if k <= 0:
                return ()
            end = len(self._windows) - (1 if exclude_last else 0)
            return tuple(self._windows[i] for i in range(end - k, end))

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()
            self._contiguous = 0
