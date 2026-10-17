"""Polling of the 'Recent alerts' list, without Streamlit: merge/dedup and back-off (ADR-009).

Each poll asks for alerts ``since`` the newest ``window_end`` already shown. ``since`` is
inclusive (contract §5), so the answer repeats alerts already shown; they are deduplicated by
``alert_id``. After a failure, retries back off exponentially from the refresh interval up to
60 s. Clocks are passed in: ``wall_now`` only labels the "unavailable since" banner (the
operator's connection, not simulated time); ``mono_now`` schedules retries.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from dashboard.source import (
    MAX_LIMIT,
    AlertQuery,
    AlertSource,
    ContractMismatchError,
    SourceError,
    SourceUnavailableError,
)
from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import StatusResponse

RECENT_LIMIT = 50
BACKOFF_CAP_S = 60.0

ErrorKind = Literal["unavailable", "mismatch", "error"]


@dataclass(frozen=True)
class PollState:
    """What the list shows and how the last poll went."""

    alerts: tuple[Alert, ...] = ()
    status: StatusResponse | None = None
    failures: int = 0
    error: str | None = None
    error_kind: ErrorKind | None = None
    unavailable_since: datetime | None = None
    retry_at: float = 0.0
    recovered: bool = False
    loaded: bool = False
    truncated: bool = False  # more new alerts than one request returns (see the history)


def merge_newest_first(
    shown: Sequence[Alert], fresh: Sequence[Alert], keep: int = RECENT_LIMIT
) -> tuple[Alert, ...]:
    """``fresh`` (newest first, as the API answers) goes on top; an ``alert_id`` appears once
    (the fresh copy wins); at most ``keep`` alerts are kept."""
    seen: set[str] = set()
    merged: list[Alert] = []
    for alert in (*fresh, *shown):
        if alert.alert_id not in seen:
            seen.add(alert.alert_id)
            merged.append(alert)
    return tuple(merged[:keep])


def backoff_s(failures: int, base_s: float, cap_s: float = BACKOFF_CAP_S) -> float:
    """Delay before the next attempt: ``base_s`` when healthy, then doubling per consecutive
    failure, never above ``cap_s``."""
    if failures <= 0:
        return base_s
    return min(base_s * 2.0 ** min(failures, 64), cap_s)


def poll(
    state: PollState,
    source: AlertSource,
    base_s: float,
    wall_now: datetime,
    mono_now: float,
) -> PollState:
    """One refresh. While backing off and not yet due, returns ``state`` unchanged."""
    if state.failures and mono_now < state.retry_at:
        return state
    since = max((a.window_end for a in state.alerts), default=None)
    query = AlertQuery() if since is None else AlertQuery(limit=MAX_LIMIT, since=since)
    try:
        source.health()
        status = source.status()
        page = source.list_alerts(query)
    except SourceError as exc:
        return _failed(state, exc, base_s, wall_now, mono_now)
    return PollState(
        alerts=merge_newest_first(state.alerts, page.items),
        status=status,
        recovered=state.failures > 0,
        loaded=True,
        truncated=since is not None and page.total > len(page.items),
    )


def needs_full_rerun(previous: PollState, state: PollState) -> bool:
    """The whole page (header clock, detail selector) must follow the list when the backend
    recovered, the simulated time moved or the listed alerts changed. Not on the first load:
    the full run that triggered it is already fresh."""
    if not previous.loaded or not state.loaded:
        return False
    if state.recovered:
        return True
    clock = (
        previous.status.last_timestamp if previous.status else None,
        state.status.last_timestamp if state.status else None,
    )
    if state.status is not None and clock[0] != clock[1]:
        return True
    return [a.alert_id for a in previous.alerts] != [a.alert_id for a in state.alerts]


def _failed(
    state: PollState, exc: SourceError, base_s: float, wall_now: datetime, mono_now: float
) -> PollState:
    kind: ErrorKind
    if isinstance(exc, SourceUnavailableError):
        kind = "unavailable"
    elif isinstance(exc, ContractMismatchError):
        kind = "mismatch"
    else:
        kind = "error"
    failures = state.failures + 1
    since = (state.unavailable_since or wall_now) if kind == "unavailable" else None
    return replace(
        state,
        alerts=state.alerts if kind == "unavailable" else (),  # stale only while unreachable
        failures=failures,
        error=str(exc),
        error_kind=kind,
        unavailable_since=since,
        retry_at=mono_now + backoff_s(failures, base_s),
        recovered=False,
        loaded=True,
    )
