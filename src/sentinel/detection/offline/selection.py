"""Choosing the operating point of the CUSUM detector on 2018 (BE-012j, ADR-008 §C).

A *cell* is one full set of detector parameters (``k``, ``h``, ``N``, ``c``, ``h_close``). Each
cell is replayed once (``replay_cells``) and scored once against the labelled leaks
(``evaluate_cells``, layer 1); the Pareto front, the selection and the leave-one-leak-out reuse
those openings and never replay again.

*Selection rule* (ADR-008 §C): among the cells with at most ``budget`` false alarms per week,
the most leaks detected, then the smallest median delay from ``start`` over the detected leaks;
ties go to the larger ``h``, then the larger ``N`` (the conservative side), then the larger
``k``, ``c`` and ``h_close`` so that the answer is always one cell.

*Leave-one-leak-out*: for each labelled leak, the openings inside its matching window are
dropped, the period shrinks by that window, and the rule picks a cell on the other leaks; the
held-out leak is then looked up in the chosen cell's full evaluation. The difference between
the in-sample and the held-out detections is the optimism of the selection.

Offline only (numpy, pandas); never imported by the server. Counts only, no confidence
intervals (ADR-008 §F).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import numpy.typing as npt
import pandas as pd

from sentinel.detection.offline.layer1 import Layer1Result, LeakOutcome, MatchRule, evaluate
from sentinel.detection.offline.sweep import Opening, median_delay, replay


@dataclass(frozen=True, slots=True)
class Cell:
    """One detector configuration; the names are the calibration artefact's."""

    k: float
    h: float
    n_min: int
    c: float
    h_close: float


def _conservative(cell: Cell) -> tuple[float, int, float, float, float]:
    return (cell.h, cell.n_min, cell.k, cell.c, cell.h_close)


def replay_cells(
    z: npt.NDArray[np.float64],
    timestamps: Sequence[datetime] | pd.DatetimeIndex,
    cells: Sequence[Cell],
) -> dict[Cell, list[Opening]]:
    """The openings of each cell over ``z`` (``sweep.replay``), one replay per cell."""
    if len(set(cells)) != len(cells):
        raise ValueError("the cells are not distinct")
    return {
        cell: replay(
            z, timestamps, k=cell.k, h=cell.h, n_min=cell.n_min, c=cell.c, h_close=cell.h_close
        )
        for cell in cells
    }


def evaluate_cells(
    openings: Mapping[Cell, Sequence[Opening]],
    leaks: pd.DataFrame,
    *,
    period: timedelta,
    match: MatchRule,
) -> dict[Cell, Layer1Result]:
    """Layer-1 result of each cell against ``leaks`` (``layer1.evaluate``)."""
    return {
        cell: evaluate([o.timestamp for o in found], leaks, period=period, match=match)
        for cell, found in openings.items()
    }


def _hours(delay: timedelta | None) -> float:
    return math.nan if delay is None else delay / timedelta(hours=1)


def cell_table(results: Mapping[Cell, Layer1Result]) -> pd.DataFrame:
    """One row per cell, in the order of ``results``: the parameters, the counts of
    ``Layer1Result``, per leak type ``detected_<type>`` / ``total_<type>``,
    ``median_delay_start_h`` (hours, over every detected leak; NaN if none) and ``missed`` (the
    pipes not detected, by ``start``, space-separated)."""
    rows = []
    for cell, result in results.items():
        row: dict[str, object] = {
            "k": cell.k,
            "h": cell.h,
            "n_min": cell.n_min,
            "c": cell.c,
            "h_close": cell.h_close,
            "openings": result.openings,
            "false_alarms": result.false_alarms,
            "false_alarms_per_week": result.false_alarms_per_week,
            "repeat_openings": result.repeat_openings,
            "detected": result.detected,
            "total": result.total,
        }
        for kind, count in result.by_type.items():
            row[f"detected_{kind}"] = count.detected
            row[f"total_{kind}"] = count.total
        delays = [leak.delay_from_start for leak in result.leaks if leak.detected]
        row["median_delay_start_h"] = _hours(median_delay(delays))
        row["missed"] = " ".join(leak.pipe for leak in result.leaks if not leak.detected)
        rows.append(row)
    return pd.DataFrame(rows)


def cell_of(row: pd.Series) -> Cell:
    return Cell(
        k=float(row["k"]),
        h=float(row["h"]),
        n_min=int(row["n_min"]),
        c=float(row["c"]),
        h_close=float(row["h_close"]),
    )


def _delay(row: pd.Series) -> float:
    value = float(row["median_delay_start_h"])
    return math.inf if math.isnan(value) else value


def pareto_front(table: pd.DataFrame) -> pd.DataFrame:
    """The cells no other cell dominates on (false alarms per week: lower, leaks detected:
    higher, median delay from ``start``: lower; no delay counts as infinite). A cell dominates
    another if it is no worse on all three and better on one. Sorted by false alarms per week,
    then detections descending."""
    points = [
        (float(r["false_alarms_per_week"]), -int(r["detected"]), _delay(r))
        for _, r in table.iterrows()
    ]
    keep = [
        i
        for i, p in enumerate(points)
        if not any(
            all(a <= b for a, b in zip(q, p, strict=True)) and q != p
            for j, q in enumerate(points)
            if j != i
        )
    ]
    front = table.iloc[keep]
    return front.sort_values(
        ["false_alarms_per_week", "detected"], ascending=[True, False], kind="stable"
    ).reset_index(drop=True)


def select(table: pd.DataFrame, budget: float) -> pd.Series | None:
    """The row the selection rule (module docstring) picks for ``budget`` false alarms per
    week, or ``None`` if no cell is within the budget."""
    if not (math.isfinite(budget) and budget >= 0):
        raise ValueError(f"budget must be a non-negative number, got {budget}")
    eligible = [r for _, r in table.iterrows() if float(r["false_alarms_per_week"]) <= budget]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda r: (-int(r["detected"]), _delay(r), *(-v for v in _conservative(cell_of(r)))),
    )


@dataclass(frozen=True, slots=True)
class LoloFold:
    """One held-out leak: the cell chosen without it (``None`` if no cell met the budget),
    whether that cell detects it and with which delay from ``start``."""

    pipe: str
    type: str
    chosen: Cell | None
    detected: bool
    delay_from_start: timedelta | None


@dataclass(frozen=True, slots=True)
class LoloSummary:
    """Leave-one-leak-out for one budget. ``stable``: every fold chose the in-sample cell.
    ``final``: the in-sample cell if stable, else the most conservative (largest ``h``, then
    ``N``) of the in-sample cell and the cells the folds chose; ``None`` if no cell anywhere met
    the budget."""

    budget: float
    in_sample: Cell | None
    in_sample_detected: int
    lolo_detected: int
    total: int
    folds: tuple[LoloFold, ...]
    distinct_cells: int
    stable: bool
    final: Cell | None


def _outcome(result: Layer1Result, pipe: str, start: datetime) -> LeakOutcome:
    return next(leak for leak in result.leaks if leak.pipe == pipe and leak.start == start)


def leave_one_leak_out(
    openings: Mapping[Cell, Sequence[Opening]],
    results: Mapping[Cell, Layer1Result],
    leaks: pd.DataFrame,
    *,
    period: timedelta,
    span: tuple[datetime, datetime],
    match: MatchRule,
    budgets: Sequence[float],
) -> list[LoloSummary]:
    """Leave-one-leak-out of the selection rule, one summary per budget (module docstring).

    ``openings`` and ``results``: the openings of every cell and their in-sample evaluation
    against all ``leaks`` with ``period`` and ``match``. ``span``: the first and last window
    timestamp of the replay; the time the openings cover is ``(first - step, last]``, with
    ``step = period / number of windows`` implied by ``period`` and ``span``. A held-out window
    shrinks the period by its overlap with that span.
    """
    if set(openings) != set(results):
        raise ValueError("openings and results must cover the same cells")
    last = span[1]
    full = cell_table(results)
    covered_from = last - period
    folds: dict[float, list[LoloFold]] = {budget: [] for budget in budgets}
    ordered = leaks.sort_values("start", kind="stable").reset_index(drop=True)
    for position in range(len(ordered)):
        row = ordered.iloc[position]
        pipe, kind = str(row["pipe"]), str(row["type"])
        start = pd.Timestamp(row["start"]).to_pydatetime()
        end = pd.Timestamp(row["end"]).to_pydatetime()
        lo, hi = start, min(end, start + match.max_delay[kind])
        overlap = max(timedelta(0), min(hi, last) - max(lo, covered_from))
        others = ordered.drop(index=ordered.index[position])
        held = {
            cell: evaluate(
                [o.timestamp for o in found if not lo <= o.timestamp <= hi],
                others,
                period=period - overlap,
                match=match,
            )
            for cell, found in openings.items()
        }
        table = cell_table(held)
        for budget in budgets:
            chosen = select(table, budget)
            if chosen is None:
                folds[budget].append(LoloFold(pipe, kind, None, False, None))
                continue
            cell = cell_of(chosen)
            outcome = _outcome(results[cell], pipe, start)
            folds[budget].append(
                LoloFold(pipe, kind, cell, outcome.detected, outcome.delay_from_start)
            )
    summaries = []
    for budget in budgets:
        picked = select(full, budget)
        in_sample = None if picked is None else cell_of(picked)
        chosen_cells = [f.chosen for f in folds[budget]]
        stable = all(c == in_sample for c in chosen_cells)
        candidates = [c for c in [in_sample, *chosen_cells] if c is not None]
        final = in_sample if stable else max(candidates, key=_conservative, default=None)
        summaries.append(
            LoloSummary(
                budget=budget,
                in_sample=in_sample,
                in_sample_detected=0 if in_sample is None else results[in_sample].detected,
                lolo_detected=sum(f.detected for f in folds[budget]),
                total=len(ordered),
                folds=tuple(folds[budget]),
                distinct_cells=len(set(chosen_cells)),
                stable=stable,
                final=final,
            )
        )
    return summaries


@dataclass(frozen=True, slots=True)
class Recovery:
    """A leak repaired inside the replay: the alert that detected it (``None`` if missed), the
    sensors of that alert (column indices) and the first window at or after the repair
    (``end``) where all of them are below ``h_close`` (``None`` if never)."""

    pipe: str
    end: datetime
    detected_at: datetime | None
    sensors: tuple[int, ...]
    recovered_at: datetime | None

    @property
    def recovery(self) -> timedelta | None:
        return None if self.recovered_at is None else self.recovered_at - self.end


def recovery_times(
    s: npt.NDArray[np.float64],
    timestamps: pd.DatetimeIndex,
    leaks: pd.DataFrame,
    result: Layer1Result,
    openings: Sequence[Opening],
    *,
    h_close: float,
) -> list[Recovery]:
    """Time from the repair to the release of every sensor of the detecting alert.

    ``s``: the ``S`` matrix of the cell (``sweep.trajectory``), one row per ``timestamps``.
    ``leaks``: the label columns plus ``repaired_in_2018``; only repaired leaks whose ``end`` is
    at or before the last timestamp are reported, in order of ``start``. ``result`` and
    ``openings``: the cell's layer-1 result and openings (an opening is found by its timestamp).
    """
    if "repaired_in_2018" not in leaks.columns:
        raise ValueError("leaks lack the column 'repaired_in_2018'")
    if s.shape[0] != len(timestamps):
        raise ValueError(f"S has {s.shape[0]} rows but there are {len(timestamps)} timestamps")
    by_time = {o.timestamp: o for o in openings}
    last = timestamps[-1].to_pydatetime()
    out = []
    ordered = leaks.sort_values("start", kind="stable")
    for pipe, start_ts, end_ts, repaired in zip(
        ordered["pipe"], ordered["start"], ordered["end"], ordered["repaired_in_2018"], strict=True
    ):
        end = pd.Timestamp(end_ts).to_pydatetime()
        if not bool(repaired) or end > last:
            continue
        outcome = _outcome(result, str(pipe), pd.Timestamp(start_ts).to_pydatetime())
        if outcome.detected_at is None:
            out.append(Recovery(str(pipe), end, None, (), None))
            continue
        sensors = by_time[outcome.detected_at].sensors
        first = int(timestamps.searchsorted(end, side="left"))
        below = (s[first:, list(sensors)] < h_close).all(axis=1)
        hits = np.flatnonzero(below)
        recovered = None if hits.size == 0 else timestamps[first + int(hits[0])].to_pydatetime()
        out.append(Recovery(str(pipe), end, outcome.detected_at, sensors, recovered))
    return out
