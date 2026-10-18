"""Layer-1 metrics of the detector: delay and false alarms per week, with counts (ADR-008 §F).

The evaluation is on one year of out-of-fold replay against the labelled leaks of that year. Only
counts n/N are reported, never confidence intervals (14 events in 2018). Offline only (needs
pandas, a dev dependency); the server never imports it.

The matching rule between an alert opening and a leak is PROVISIONAL: the evaluation owner has
not registered it yet (Q-UI-06, ADR-008 §G). It is a required argument everywhere, so no value
of it is ever implied.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

LEAK_TYPES = ("abrupt", "incipient")  # the ``type`` column of the label files (data.md §6)
_REQUIRED_COLUMNS = ("pipe", "start", "end", "type", "peak")
_DATE_COLUMNS = ("start", "end", "peak")


@dataclass(frozen=True)
class MatchRule:
    """PROVISIONAL rule that decides which leak an alert opening detects (Q-UI-06 is open).

    The matching window of a leak is ``[start, min(end, start + max_delay[type])]``, both ends
    included: ``end`` is the repair, ``max_delay`` the longest delay still counted as a
    detection of that leak type. Both numbers are inputs, never defaults.
    """

    max_delay: Mapping[str, timedelta]

    def __post_init__(self) -> None:
        unknown = sorted(set(self.max_delay) - set(LEAK_TYPES))
        if unknown:
            raise ValueError(f"max_delay has unknown leak types {unknown}; known: {LEAK_TYPES}")
        negative = sorted(t for t, delay in self.max_delay.items() if delay < timedelta(0))
        if negative:
            raise ValueError(f"max_delay cannot be negative (types {negative})")


@dataclass(frozen=True, slots=True)
class LeakOutcome:
    """One labelled leak: detected or not, and the delay of the detection.

    ``delay_from_start`` and ``delay_from_peak`` are ``detected_at`` minus the label ``start``
    and ``peak`` (ADR-008 §F); the second is negative if the alert opened before the peak.
    Both are ``None`` for a missed leak.
    """

    pipe: str
    type: str
    start: datetime
    peak: datetime
    detected_at: datetime | None
    delay_from_start: timedelta | None
    delay_from_peak: timedelta | None

    @property
    def detected(self) -> bool:
        return self.detected_at is not None


@dataclass(frozen=True, slots=True)
class TypeCount:
    """n/N of one leak type: ``detected`` of ``total``, and the pipes of the missed ones (in
    order of ``start``)."""

    detected: int
    total: int
    missed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Layer1Result:
    """Layer-1 outcome of one set of openings against one set of labelled leaks.

    Every opening is exactly one of: a detection (the first opening inside the window of a leak
    not yet detected), a repeat (inside the windows of leaks that are all detected already) or
    a false alarm (outside the window of every labelled leak, ADR-008 §C): ``openings ==
    detected + repeat_openings + false_alarms``. A repeat is neither a hit nor a false alarm
    because the detector has no closing event (ADR-007 §4): a persistent leak can open another
    alert once another group of sensors crosses ``h``; counting it as a hit would inflate the
    detections and counting it as a false alarm would punish the detector for a real leak.
    ``leaks`` are in order of ``start``. ``false_alarms_per_week`` is ``false_alarms`` over
    ``period`` in weeks.
    """

    leaks: tuple[LeakOutcome, ...]
    by_type: Mapping[str, TypeCount]
    openings: int
    false_alarms: int
    false_alarm_times: tuple[datetime, ...]
    repeat_openings: int
    period: timedelta
    weeks: float
    false_alarms_per_week: float

    @property
    def detected(self) -> int:
        return sum(count.detected for count in self.by_type.values())

    @property
    def total(self) -> int:
        return sum(count.total for count in self.by_type.values())


def evaluate(
    opening_times: Sequence[datetime],
    leaks: pd.DataFrame,
    *,
    period: timedelta,
    match: MatchRule,
) -> Layer1Result:
    """Match alert openings with labelled leaks and count (ADR-008 §F).

    ``opening_times``: the timestamp of each alert opening (the end of its window), e.g. from
    ``sweep.replay``. ``leaks``: the columns of ``leaks_2018.csv`` (``pipe``, ``start``,
    ``end``, ``type`` in {abrupt, incipient}, ``peak``; others are ignored), with the dates
    parsed as ``datetime64`` and in the same time zone convention (aware or naive) as the
    openings. ``period``: the length of time the openings cover, for the false alarms per week
    (for a replay of T windows, ``T * interval``). ``match``: the PROVISIONAL rule (see
    ``MatchRule``).

    Each opening, in time order, detects the first leak (by ``start``; ties by row order) whose
    matching window contains it and that is not detected yet. An opening in a window but with
    every leak of those windows already detected is a repeat (``repeat_openings``); outside
    every window it is a false alarm. All leaks of ``leaks`` count in N, also those that start
    after the evaluated period; selecting them is the caller's job. ``ValueError`` for
    malformed leaks, a missing ``max_delay`` or a non-positive ``period``.
    """
    if period <= timedelta(0):
        raise ValueError(f"period must be positive, got {period}")
    labelled = _labelled_leaks(leaks, match, opening_times)
    windows = [
        (leak.start, min(leak.end, leak.start + match.max_delay[leak.type])) for leak in labelled
    ]
    detected_at: list[datetime | None] = [None] * len(windows)
    false_alarm_times: list[datetime] = []
    repeats = 0
    for opening in sorted(opening_times):
        inside = [j for j, (lo, hi) in enumerate(windows) if lo <= opening <= hi]
        undetected = [j for j in inside if detected_at[j] is None]
        if undetected:
            detected_at[undetected[0]] = opening
        elif inside:
            repeats += 1
        else:
            false_alarm_times.append(opening)
    outcomes = tuple(
        LeakOutcome(
            pipe=leak.pipe,
            type=leak.type,
            start=leak.start,
            peak=leak.peak,
            detected_at=found,
            delay_from_start=None if found is None else found - leak.start,
            delay_from_peak=None if found is None else found - leak.peak,
        )
        for leak, found in zip(labelled, detected_at, strict=True)
    )
    weeks = period / timedelta(weeks=1)
    return Layer1Result(
        leaks=outcomes,
        by_type={kind: _count(kind, outcomes) for kind in LEAK_TYPES},
        openings=len(opening_times),
        false_alarms=len(false_alarm_times),
        false_alarm_times=tuple(false_alarm_times),
        repeat_openings=repeats,
        period=period,
        weeks=weeks,
        false_alarms_per_week=len(false_alarm_times) / weeks,
    )


def _count(kind: str, outcomes: Sequence[LeakOutcome]) -> TypeCount:
    of_kind = [leak for leak in outcomes if leak.type == kind]
    return TypeCount(
        detected=sum(leak.detected for leak in of_kind),
        total=len(of_kind),
        missed=tuple(leak.pipe for leak in of_kind if not leak.detected),
    )


@dataclass(frozen=True, slots=True)
class _Leak:
    pipe: str
    type: str
    start: datetime
    end: datetime
    peak: datetime


def _labelled_leaks(
    leaks: pd.DataFrame, match: MatchRule, opening_times: Sequence[datetime]
) -> list[_Leak]:
    """The validated leaks in order of ``start`` (ties by row order), as plain ``datetime``."""
    missing = [c for c in _REQUIRED_COLUMNS if c not in leaks.columns]
    if missing:
        raise ValueError(f"leaks lack the columns {missing}")
    if leaks.empty:
        return []
    for column in _DATE_COLUMNS:
        if not pd.api.types.is_datetime64_any_dtype(leaks[column]):
            raise ValueError(
                f"leaks column {column!r} must be datetime64 (parse the dates), "
                f"got {leaks[column].dtype}"
            )
    aware = {leaks[column].dt.tz is not None for column in _DATE_COLUMNS}
    if len(aware) > 1 or (opening_times and aware != {opening_times[0].tzinfo is not None}):
        raise ValueError(
            "time zone mismatch: leak dates and opening times must be all aware or all naive"
        )
    unknown = sorted(set(leaks["type"]) - set(LEAK_TYPES))
    if unknown:
        raise ValueError(f"leak type must be one of {LEAK_TYPES}, got {unknown}")
    no_delay = sorted(set(leaks["type"]) - set(match.max_delay))
    if no_delay:
        raise ValueError(f"the matching rule has no max_delay for the leak types {no_delay}")
    repaired_first = leaks.loc[leaks["end"] < leaks["start"], "pipe"]
    if len(repaired_first):
        raise ValueError(f"leaks with end before start: {sorted(map(str, repaired_first))}")
    dates = {
        column: [pd.Timestamp(t).to_pydatetime() for t in leaks[column]] for column in _DATE_COLUMNS
    }
    found = [
        _Leak(str(pipe), str(kind), start, end, peak)
        for pipe, kind, start, end, peak in zip(
            leaks["pipe"],
            leaks["type"],
            dates["start"],
            dates["end"],
            dates["peak"],
            strict=True,
        )
    ]
    return sorted(found, key=lambda leak: leak.start)
