"""Layer-2 metrics of one 2019 run (ADR-010). Pure: alerts and labels in, counts out.

Matching is the detection owner's ``layer1.evaluate`` with the rules fixed in ADR-010:

- *primary*: the official BattLeDIM time window ``[start, end]`` over every labelled leak
  (``max_delay`` longer than any label window, so the leaks inherited from 2018 keep theirs);
- *operator*: detected within 7 days of ``start`` — the only source of false alarms, because
  under the primary window a leak active all year makes every alert a hit or a repeat;
- *new in year*: the primary rule over the leaks that start in the evaluated year.

Offline only (pandas, a dev dependency); the server never imports it.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from sentinel.detection.offline.layer1 import LEAK_TYPES, Layer1Result, MatchRule, evaluate
from sentinel.schemas.alerts import Alert

PRIMARY_MAX_DELAY = timedelta(days=731)  # > the longest label window (p257: 722 days)
OPERATOR_MAX_DELAY = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class Evaluation:
    primary: Layer1Result
    operator: Layer1Result
    new_in_year: Layer1Result
    year_start: datetime
    delay_p50: timedelta | None  # primary view, from the label ``start`` (nearest rank)
    delay_p90: timedelta | None
    delay_max: timedelta | None
    peak_delay_p50: timedelta | None  # primary view, from the label ``peak``
    peak_delay_max: timedelta | None
    # pipe -> suspected_onset minus start, from the first alert (stored order) with an onset
    # among those opened at the detection time (several alerts can share a window_end).
    onset_error: dict[str, timedelta | None]

    @property
    def inherited(self) -> tuple[str, ...]:
        """Leaks that started before the evaluated year; they count (ADR-010 §3)."""
        return tuple(o.pipe for o in self.primary.leaks if o.start < self.year_start)

    @property
    def false_alarms_per_week(self) -> float:
        return self.operator.false_alarms_per_week


def _rule(max_delay: timedelta) -> MatchRule:
    return MatchRule(max_delay=dict.fromkeys(LEAK_TYPES, max_delay))


def _nearest_rank(values: Sequence[timedelta], q: float) -> timedelta | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def evaluate_run(
    alerts: Sequence[Alert], leaks: pd.DataFrame, *, period: timedelta, year_start: datetime
) -> Evaluation:
    """``alerts``: every alert of the run; the detection time is ``window_end`` (simulated).
    ``leaks``: label columns ``pipe, start, end, type, peak`` as tz-aware ``datetime64``.
    ``period``: the replayed time span (for false alarms per week)."""
    too_long = leaks[(leaks["end"] - leaks["start"]) > pd.Timedelta(PRIMARY_MAX_DELAY)]
    if not too_long.empty:
        raise ValueError(
            f"label windows longer than {PRIMARY_MAX_DELAY.days} days would be cut "
            f"(ADR-010 §1): {sorted(too_long['pipe'])}"
        )
    openings = [a.window_end for a in alerts]
    primary = evaluate(openings, leaks, period=period, match=_rule(PRIMARY_MAX_DELAY))
    operator = evaluate(openings, leaks, period=period, match=_rule(OPERATOR_MAX_DELAY))
    new = leaks[leaks["start"] >= pd.Timestamp(year_start)]
    new_in_year = evaluate(openings, new, period=period, match=_rule(PRIMARY_MAX_DELAY))

    delays = [o.delay_from_start for o in primary.leaks if o.delay_from_start is not None]
    peak_delays = [o.delay_from_peak for o in primary.leaks if o.delay_from_peak is not None]
    onset_error: dict[str, timedelta | None] = {}
    for outcome in primary.leaks:
        onsets = [
            a.suspected_onset
            for a in alerts
            if a.window_end == outcome.detected_at and a.suspected_onset is not None
        ]
        onset_error[outcome.pipe] = onsets[0] - outcome.start if onsets else None
    return Evaluation(
        primary=primary,
        operator=operator,
        new_in_year=new_in_year,
        year_start=year_start,
        delay_p50=_nearest_rank(delays, 0.50),
        delay_p90=_nearest_rank(delays, 0.90),
        delay_max=max(delays, default=None),
        peak_delay_p50=_nearest_rank(peak_delays, 0.50),
        peak_delay_max=max(peak_delays, default=None),
        onset_error=onset_error,
    )
