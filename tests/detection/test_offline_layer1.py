"""Layer-1 metrics: detection delay and false alarms per week (BE-012f, ADR-008 §F).

Everything here is synthetic: the leaks ("p1", "p2", ...), their start, peak and end, the
matching tolerances, the opening times and the period are invented for the tests, labelled as
such, and none comes from a real network or from the 2018/2019 label files. The matching rule
is PROVISIONAL (Q-UI-06 is open): the tests pin the rule as implemented, not a recommendation.

Times are written as window indices on the test grid (``ts(i)``, 5 minutes apart), so the
expected delays are exact multiples of ``INTERVAL``.
"""

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
import pytest

from sentinel.detection.offline import layer1
from sentinel.detection.offline.layer1 import (
    LeakOutcome,
    MatchRule,
    TypeCount,
    evaluate,
)
from tests.conftest import INTERVAL, ts


@dataclass(frozen=True)
class Leak:
    """A labelled leak in window indices; ``peak`` defaults to the start."""

    pipe: str
    kind: str
    start: int
    end: int
    peak: int | None = None


def leaks_frame(*leaks: Leak) -> pd.DataFrame:
    """The columns of ``leaks_2018.csv`` (data.md §6), parsed as dates."""
    return pd.DataFrame(
        {
            "pipe": [leak.pipe for leak in leaks],
            "start": pd.to_datetime([ts(leak.start) for leak in leaks]),
            "end": pd.to_datetime([ts(leak.end) for leak in leaks]),
            "leak_size": [0.1] * len(leaks),  # invented
            "type": [leak.kind for leak in leaks],
            "peak": pd.to_datetime(
                [ts(leak.start if leak.peak is None else leak.peak) for leak in leaks]
            ),
        }
    )


# Invented tolerances: an abrupt leak must be seen within 20 windows, an incipient one within 100.
RULE = MatchRule(max_delay={"abrupt": 20 * INTERVAL, "incipient": 100 * INTERVAL})
PERIOD = timedelta(weeks=2)


def opened(*indices: int) -> list[datetime]:
    return [ts(i) for i in indices]


def check(
    openings: Sequence[datetime], *leaks: Leak, rule: MatchRule = RULE, period: timedelta = PERIOD
) -> layer1.Layer1Result:
    return evaluate(openings, leaks_frame(*leaks), period=period, match=rule)


def outcome(result: layer1.Layer1Result, pipe: str) -> LeakOutcome:
    return next(leak for leak in result.leaks if leak.pipe == pipe)


# --- 1. Detection and delay ----------------------------------------------------------------


def test_a_detected_leak_reports_the_delay_from_start_and_from_peak() -> None:
    result = check(opened(108), Leak("p1", "abrupt", start=100, end=400))

    assert outcome(result, "p1") == LeakOutcome(
        pipe="p1",
        type="abrupt",
        start=ts(100),
        peak=ts(100),
        detected_at=ts(108),
        delay_from_start=8 * INTERVAL,
        delay_from_peak=8 * INTERVAL,
    )
    assert outcome(result, "p1").detected
    leak = outcome(result, "p1")  # plain datetime and timedelta, not their pandas subclasses
    assert [type(leak.start), type(leak.peak), type(leak.detected_at)] == [datetime] * 3
    assert [type(leak.delay_from_start), type(leak.delay_from_peak)] == [timedelta] * 2


def test_the_delay_from_peak_is_negative_if_the_leak_is_detected_before_its_peak() -> None:
    result = check(opened(230), Leak("p1", "incipient", start=200, end=900, peak=260))

    leak = outcome(result, "p1")
    assert leak.delay_from_start == 30 * INTERVAL
    assert leak.delay_from_peak == -30 * INTERVAL  # reported as it is


def test_a_missed_leak_has_no_delay() -> None:
    result = check([], Leak("p1", "abrupt", start=100, end=400))

    assert outcome(result, "p1") == LeakOutcome(
        pipe="p1",
        type="abrupt",
        start=ts(100),
        peak=ts(100),
        detected_at=None,
        delay_from_start=None,
        delay_from_peak=None,
    )
    assert not outcome(result, "p1").detected


def test_leaks_are_listed_by_start_whatever_the_order_of_the_labels() -> None:
    result = check(
        [], Leak("late", "abrupt", start=300, end=400), Leak("early", "abrupt", start=100, end=200)
    )

    assert [leak.pipe for leak in result.leaks] == ["early", "late"]


# --- 2. The matching window ----------------------------------------------------------------


def test_the_window_runs_from_start_to_start_plus_max_delay_inclusive() -> None:
    leak = Leak("p1", "abrupt", start=100, end=400)  # abrupt window: 100 .. 120

    inside = [check(opened(i), leak) for i in (100, 120)]
    outside = [check(opened(i), leak) for i in (99, 121)]

    assert [r.false_alarms for r in inside] == [0, 0]
    assert [outcome(r, "p1").detected for r in inside] == [True, True]
    assert [r.false_alarms for r in outside] == [1, 1]
    assert [outcome(r, "p1").detected for r in outside] == [False, False]


def test_the_window_closes_at_the_repair_if_it_comes_before_max_delay() -> None:
    leak = Leak("p1", "abrupt", start=100, end=105)  # repaired after 5 windows

    assert check(opened(105), leak).false_alarms == 0
    assert check(opened(106), leak).false_alarms == 1


def test_a_leak_repaired_in_its_first_window_still_has_a_window() -> None:
    result = check(opened(100), Leak("p1", "abrupt", start=100, end=100))

    assert (result.false_alarms, outcome(result, "p1").detected) == (0, True)


def test_max_delay_is_per_leak_type() -> None:
    # 50 windows after the start: inside the incipient window (100), outside the abrupt one (20).
    abrupt = check(opened(150), Leak("p1", "abrupt", start=100, end=400))
    incipient = check(opened(150), Leak("p1", "incipient", start=100, end=400))

    assert (abrupt.false_alarms, outcome(abrupt, "p1").detected) == (1, False)
    assert (incipient.false_alarms, outcome(incipient, "p1").detected) == (0, True)


# --- 3. False alarms -----------------------------------------------------------------------


def test_an_opening_outside_every_window_is_a_false_alarm_even_with_a_leak_active() -> None:
    # p1 is active from 100 to 400 (its abrupt window ends at 120): an opening at 200 is a
    # false alarm although a leak is active (ADR-008 §C).
    result = check(opened(110, 200), Leak("p1", "abrupt", start=100, end=400))

    assert result.false_alarms == 1
    assert result.false_alarm_times == (ts(200),)
    assert outcome(result, "p1").detected_at == ts(110)


def test_false_alarms_per_week_divide_by_the_evaluated_period_in_weeks() -> None:
    result = check(opened(10, 20, 30), period=timedelta(weeks=2))

    assert result.false_alarms == 3
    assert result.weeks == 2.0
    assert result.false_alarms_per_week == 1.5


def test_openings_are_counted_in_any_order() -> None:
    leak = Leak("p1", "abrupt", start=100, end=400)

    assert check(opened(300, 110, 10), leak) == check(opened(10, 110, 300), leak)


# --- 4. Repeated openings and several leaks ------------------------------------------------


def test_a_repeated_opening_for_a_detected_leak_is_neither_a_hit_nor_a_false_alarm() -> None:
    result = check(opened(105, 110, 118), Leak("p1", "abrupt", start=100, end=400))

    assert result.repeat_openings == 2
    assert result.false_alarms == 0
    assert result.openings == 3  # = 1 detection + 2 repeats + 0 false alarms
    assert outcome(result, "p1").detected_at == ts(105)  # the first opening is the detection


def test_an_opening_matches_the_first_undetected_leak_by_start_whose_window_contains_it() -> None:
    # Windows: p1 100..120, p2 110..130. Both contain 112 and 115; p1 is first by start.
    leaks = (Leak("p2", "abrupt", start=110, end=400), Leak("p1", "abrupt", start=100, end=400))

    result = check(opened(112, 115, 119), *leaks)

    assert outcome(result, "p1").detected_at == ts(112)
    assert outcome(result, "p2").detected_at == ts(115)  # p1 is detected: the next one is p2's
    assert result.repeat_openings == 1  # 119: both windows contain it, both detected
    assert result.false_alarms == 0


def test_every_opening_is_exactly_a_detection_a_repeat_or_a_false_alarm() -> None:
    result = check(
        opened(5, 105, 110, 190, 204, 205, 400),
        Leak("p1", "abrupt", start=100, end=400),  # window 100..120
        Leak("p2", "abrupt", start=200, end=400),  # window 200..220
        Leak("p3", "incipient", start=900, end=1000),  # never detected
    )

    # 105 detects p1, 110 repeats, 204 detects p2, 205 repeats; 5, 190 and 400 are outside.
    assert (result.detected, result.repeat_openings, result.false_alarms) == (2, 2, 3)
    assert result.openings == result.detected + result.repeat_openings + result.false_alarms


def test_leaks_with_the_same_start_are_taken_in_the_order_of_the_rows() -> None:
    result = check(
        opened(105, 106),
        Leak("second", "abrupt", start=100, end=400),
        Leak("first", "abrupt", start=100, end=400),
    )

    assert [(leak.pipe, leak.detected_at) for leak in result.leaks] == [
        ("second", ts(105)),
        ("first", ts(106)),
    ]


# --- 5. Counts n/N per type ----------------------------------------------------------------


def test_counts_per_type_list_the_missed_leaks() -> None:
    result = check(
        opened(105, 520),
        Leak("p1", "abrupt", start=100, end=400),  # detected
        Leak("p2", "abrupt", start=1000, end=1400),  # missed
        Leak("p3", "incipient", start=500, end=900),  # detected
        Leak("p4", "incipient", start=2000, end=2600),  # missed
        Leak("p5", "incipient", start=3000, end=3600),  # missed
    )

    assert result.by_type == {
        "abrupt": TypeCount(detected=1, total=2, missed=("p2",)),
        "incipient": TypeCount(detected=1, total=3, missed=("p4", "p5")),
    }
    assert (result.detected, result.total) == (2, 5)


def test_both_types_are_reported_even_without_leaks() -> None:
    result = check(opened(10))

    assert result.by_type == {
        "abrupt": TypeCount(detected=0, total=0, missed=()),
        "incipient": TypeCount(detected=0, total=0, missed=()),
    }
    assert result.leaks == ()
    assert result.false_alarms == 1


# --- 6. Input validation -------------------------------------------------------------------


def test_every_leak_type_needs_a_max_delay() -> None:
    with pytest.raises(ValueError, match="incipient"):
        check([], Leak("p1", "incipient", start=1, end=9), rule=MatchRule({"abrupt": INTERVAL}))


def test_a_max_delay_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="max_delay"):
        MatchRule(max_delay={"abrupt": -INTERVAL, "incipient": INTERVAL})


def test_a_leak_type_must_be_abrupt_or_incipient() -> None:
    with pytest.raises(ValueError, match="type"):
        check([], Leak("p1", "sudden", start=1, end=9))


def test_a_leak_repaired_before_it_starts_is_refused() -> None:
    with pytest.raises(ValueError, match="end"):
        check([], Leak("p1", "abrupt", start=9, end=1))


def test_the_period_must_be_positive() -> None:
    with pytest.raises(ValueError, match="period"):
        check([], period=timedelta(0))


def test_the_leak_columns_are_required() -> None:
    frame = leaks_frame(Leak("p1", "abrupt", start=1, end=9)).drop(columns=["peak"])

    with pytest.raises(ValueError, match="peak"):
        evaluate([], frame, period=PERIOD, match=RULE)


def test_the_leak_dates_must_be_parsed() -> None:
    frame = leaks_frame(Leak("p1", "abrupt", start=1, end=9))
    frame["start"] = frame["start"].astype(str)

    with pytest.raises(ValueError, match="datetime"):
        evaluate([], frame, period=PERIOD, match=RULE)


def test_naive_leaks_and_aware_openings_cannot_be_mixed() -> None:
    frame = leaks_frame(Leak("p1", "abrupt", start=1, end=9))
    for column in ("start", "end", "peak"):
        frame[column] = frame[column].dt.tz_localize(None)

    with pytest.raises(ValueError, match="time zone"):
        evaluate(opened(5), frame, period=PERIOD, match=RULE)


def test_no_parameter_has_a_default() -> None:
    for function in (evaluate, MatchRule):
        defaults = [
            name
            for name, parameter in inspect.signature(function).parameters.items()
            if parameter.default is not inspect.Parameter.empty
        ]
        assert defaults == [], f"{function.__name__} has defaults: {defaults}"
