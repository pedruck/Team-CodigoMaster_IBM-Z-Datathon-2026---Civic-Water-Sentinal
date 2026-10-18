"""Pareto front, selection rule, leave-one-leak-out and recovery (BE-012j, ADR-008 §C).

Everything is hand-made: the tables, the openings, the leaks and the ``z`` steps are invented
so that every expectation can be worked out by hand. None of them is a result.
"""

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from sentinel.detection.offline.layer1 import MatchRule
from sentinel.detection.offline.selection import (
    Cell,
    cell_of,
    cell_table,
    evaluate_cells,
    leave_one_leak_out,
    pareto_front,
    recovery_times,
    replay_cells,
    select,
)
from sentinel.detection.offline.sweep import Opening, trajectory


def row(h: float, n: int, fa: float, detected: int, delay: float) -> dict[str, object]:
    return {
        "k": 0.5,
        "h": h,
        "n_min": n,
        "c": 2.0,
        "h_close": h / 2,
        "false_alarms_per_week": fa,
        "detected": detected,
        "median_delay_start_h": delay,
    }


def hand_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            row(10, 1, 0.5, 10, 5.0),  # a
            row(11, 1, 1.0, 12, 4.0),  # b
            row(12, 1, 1.0, 11, 3.0),  # c: fewer leaks than b, but faster
            row(13, 1, 2.0, 12, 6.0),  # d: dominated by b
            row(14, 1, 0.5, 10, math.nan),  # e: no delay = infinite: dominated by a
            row(15, 1, 3.0, 13, math.nan),  # f: the most leaks
        ]
    )


def test_the_pareto_front_keeps_the_non_dominated_cells_by_false_alarms() -> None:
    front = pareto_front(hand_table())

    assert front["h"].tolist() == [10, 11, 12, 15]


def test_select_maximises_detections_within_the_budget_then_minimises_the_delay() -> None:
    table = hand_table()

    picked = select(table, 1.0)
    assert picked is not None and picked["h"] == 11  # b: 12 leaks
    picked = select(table, 0.5)
    assert picked is not None and picked["h"] == 10  # a and e tie on leaks; a has a delay
    assert select(table, 0.1) is None
    with pytest.raises(ValueError, match="budget"):
        select(table, -1.0)


def test_ties_go_to_the_larger_h_then_the_larger_n() -> None:
    table = pd.DataFrame([row(8, 3, 1.0, 5, 2.0), row(9, 1, 1.0, 5, 2.0), row(9, 2, 1.0, 5, 2.0)])

    picked = select(table, 1.0)

    assert picked is not None
    assert cell_of(picked) == Cell(k=0.5, h=9.0, n_min=2, c=2.0, h_close=4.5)


# --- Leave-one-leak-out ----------------------------------------------------------------------

T0 = datetime(2018, 1, 1)
LAST = datetime(2018, 1, 29)  # the replay covers (T0, LAST]: four weeks
PERIOD = LAST - T0
MATCH = MatchRule({"abrupt": timedelta(days=7), "incipient": timedelta(days=7)})
X = Cell(k=0.5, h=10.0, n_min=1, c=2.0, h_close=5.0)
Y = Cell(k=0.5, h=20.0, n_min=1, c=2.0, h_close=10.0)


def day(d: int, hour: int = 0) -> datetime:
    return datetime(2018, 1, d, hour)


def openings(*times: datetime) -> list[Opening]:
    return [Opening(i, t, (0,), 1.0, None) for i, t in enumerate(times)]


def three_leaks() -> pd.DataFrame:
    """Three leaks whose matching windows ([start, end]) do not overlap."""
    return pd.DataFrame(
        {
            "pipe": ["L1", "L2", "L3"],
            "start": [day(3), day(12), day(20)],
            "end": [day(6), day(14), day(23)],
            "type": ["abrupt", "abrupt", "incipient"],
            "peak": [day(3), day(12), day(21)],
            "repaired_in_2018": [True, True, False],
        }
    )


def hand_openings() -> dict[Cell, list[Opening]]:
    # X detects the three leaks 1 h after their start and has four false alarms (1.0 per week);
    # Y detects L2 and L3 6 h after their start and has one false alarm (0.25 per week).
    return {
        X: openings(day(3, 1), day(8), day(9), day(10), day(12, 1), day(17), day(20, 1)),
        Y: openings(day(9), day(12, 6), day(20, 6)),
    }


def lolo(budgets: list[float]):  # type: ignore[no-untyped-def]
    found = hand_openings()
    results = evaluate_cells(found, three_leaks(), period=PERIOD, match=MATCH)
    return leave_one_leak_out(
        found,
        results,
        three_leaks(),
        period=PERIOD,
        span=(T0 + timedelta(minutes=5), LAST),
        match=MATCH,
        budgets=budgets,
    )


def test_leaving_a_leak_out_shrinks_the_period_and_can_change_the_choice() -> None:
    # Budget 1/week: in sample X (3/3, exactly 1.0 per week). Without L1 or L3 (3 days of
    # window) X keeps its 4 false alarms over 25 days = 1.12 per week: over budget, Y is chosen
    # and Y misses L1. Without L2 (2 days) X has 4 over 26 days = 1.08: Y again, and Y detects L2.
    (summary,) = lolo([1.0])

    assert summary.in_sample == X
    assert summary.in_sample_detected == 3
    assert [(f.pipe, f.chosen, f.detected) for f in summary.folds] == [
        ("L1", Y, False),
        ("L2", Y, True),
        ("L3", Y, True),
    ]
    assert summary.folds[1].delay_from_start == timedelta(hours=6)
    assert (summary.lolo_detected, summary.total) == (2, 3)
    assert summary.distinct_cells == 1
    assert not summary.stable
    assert summary.final == Y  # the most conservative of the chosen cells: larger h


def test_a_stable_choice_keeps_the_in_sample_cell() -> None:
    (summary,) = lolo([2.0])

    assert [f.chosen for f in summary.folds] == [X, X, X]
    assert summary.stable
    assert summary.final == X
    assert summary.lolo_detected == 3


def test_no_cell_within_the_budget_gives_no_operating_point() -> None:
    (summary,) = lolo([0.1])

    assert summary.in_sample is None
    assert summary.final is None
    assert summary.lolo_detected == 0


def test_cell_table_counts_and_delays() -> None:
    results = evaluate_cells(hand_openings(), three_leaks(), period=PERIOD, match=MATCH)

    table = cell_table(results)

    assert table[["h", "false_alarms", "detected", "detected_abrupt", "missed"]].to_dict(
        "records"
    ) == [
        {"h": 10.0, "false_alarms": 4, "detected": 3, "detected_abrupt": 2, "missed": ""},
        {"h": 20.0, "false_alarms": 1, "detected": 2, "detected_abrupt": 1, "missed": "L1"},
    ]
    assert table["median_delay_start_h"].tolist() == [1.0, 6.0]


# --- Recovery after a repair -----------------------------------------------------------------


def test_recovery_after_a_repair_is_the_analytic_decay_from_the_cap() -> None:
    # One sensor, k = 0.5, h = 8, c = 2 (cap 16), h_close = 4. z = 4.5 on windows 10..19 takes S
    # to the cap; the repair is at window 20, after which z = 0: S = 15.5, 15.0, ... and is first
    # below h_close at window 44, i.e. (c*h - h_close)/k = 24 windows after the repair.
    times = pd.date_range("2018-01-01 00:05", periods=80, freq="5min")
    z = np.zeros((80, 1))
    z[10:20, 0] = 4.5
    cell = Cell(k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0)
    leaks = pd.DataFrame(
        {
            "pipe": ["fixed", "missed", "never repaired", "repaired later"],
            "start": [times[9], times[60], times[30], times[70]],
            "end": [times[20], times[65], times[79], times[79] + timedelta(hours=1)],
            "type": ["abrupt"] * 4,
            "peak": [times[9], times[60], times[30], times[70]],
            "repaired_in_2018": [True, True, False, True],
        }
    )
    found = replay_cells(z, times, [cell])
    result = evaluate_cells(found, leaks, period=timedelta(minutes=5 * 80), match=MATCH)[cell]

    recoveries = recovery_times(
        trajectory(z, k=0.5, h=8.0, c=2.0), times, leaks, result, found[cell], h_close=4.0
    )

    assert [r.pipe for r in recoveries] == ["fixed", "missed"]
    fixed, missed = recoveries
    assert fixed.detected_at == times[12].to_pydatetime()  # S: 4, 8 (= h), 12
    assert fixed.sensors == (0,)
    assert fixed.recovered_at == times[44].to_pydatetime()
    assert fixed.recovery == (2.0 * 8.0 - 4.0) / 0.5 * timedelta(minutes=5)
    assert missed.detected_at is None
    assert missed.recovery is None


def test_recovery_needs_the_repair_column() -> None:
    with pytest.raises(ValueError, match="repaired_in_2018"):
        recovery_times(
            np.zeros((1, 1)),
            pd.DatetimeIndex([T0]),
            three_leaks().drop(columns="repaired_in_2018"),
            evaluate_cells({X: []}, three_leaks(), period=PERIOD, match=MATCH)[X],
            [],
            h_close=1.0,
        )


def test_replay_cells_refuses_repeated_cells() -> None:
    with pytest.raises(ValueError, match="distinct"):
        replay_cells(np.zeros((3, 1)), pd.date_range("2018-01-01", periods=3, freq="5min"), [X, X])
