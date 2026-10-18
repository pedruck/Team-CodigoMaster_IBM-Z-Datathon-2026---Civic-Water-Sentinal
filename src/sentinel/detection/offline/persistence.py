"""Displacement and persistence of ``z`` at the labelled abrupt leaks of 2018 (ADR-008 §D, BE-012j).

The operational definitions of the persistence test and of the shift ``delta`` behind ``k``
(ADR-008 §C, §G), fixed in code before any out-of-fold residual of the XGBoost is read. For an
abrupt leak starting at ``start``, every quantity is a mean of ``z`` per sensor over windows
whose timestamp ``t`` (the end of the window, contract §1) lies in a half-open span, minus the
same sensor's mean over the ``baseline`` before the leak, ``(start - baseline, start]``:

- *initial displacement*: over ``(start, start + initial]``;
- *remaining displacement*: over ``(start + remaining_after, start + remaining_after + initial]``,
  the same length and, for ``remaining_after`` = 24 h, the same hours of the day as the initial one;
- *sustained displacement*: over ``(start, start + sustained]``.

Subtracting the baseline isolates the change the leak causes from the offset left by the other
leaks active at the time (one is almost always active in 2018, ADR-008 Context).

The *affected sensors* of a leak are the ``n_top`` with the largest initial displacement (ties by
sensor order) and its *persistence* is the sum of their remaining displacements over the sum of
their initial ones (undefined, ``None``, if that sum is not positive). The weakest leak's
sustained shift on its ``N``-th most displaced sensor is ``delta_N`` (``delta_by_rank``), and
``k = delta_N / 2`` for the cells with that ``N``.

Offline only (pandas); never imported by the server. Reads ``leaks_2018.csv`` only.
"""

import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd

from sentinel.data.manifest import sha256
from sentinel.detection.offline.oof import refuse_2019

LEAKS_2018_FILE = "leaks_2018.csv"
ABRUPT = "abrupt"


def load_leaks_2018(data_dir: Path) -> tuple[pd.DataFrame, str]:
    """``<data_dir>/v1/leaks_2018.csv`` with the dates parsed, and its sha256, checked against
    ``MANIFEST.json`` (data v1 is sealed). Never opens another label file; ``ValueError`` for a
    path with ``2019`` or a hash that differs from the manifest."""
    refuse_2019(data_dir)
    v1 = data_dir / "v1"
    manifest = json.loads((v1 / "MANIFEST.json").read_text(encoding="utf-8"))
    entry = manifest.get("files", {}).get(LEAKS_2018_FILE)
    if entry is None:
        raise ValueError(f"MANIFEST.json of {v1} has no entry for {LEAKS_2018_FILE}")
    digest = sha256(v1 / LEAKS_2018_FILE)
    if digest != entry["sha256"]:
        raise ValueError(
            f"sha256 of {v1 / LEAKS_2018_FILE} is {digest}, MANIFEST.json says {entry['sha256']}"
        )
    leaks = pd.read_csv(v1 / LEAKS_2018_FILE, parse_dates=["start", "end", "peak"])
    return leaks, digest


@dataclass(frozen=True, slots=True)
class LeakDisplacement:
    """One abrupt leak. ``top_sensors``: the ``n_top`` affected sensors, by initial displacement
    descending; ``initial`` and ``remaining``: their displacements, same order. ``persistence``:
    ``sum(remaining) / sum(initial)``, ``None`` if ``sum(initial) <= 0``. ``sustained``: the
    ``max_rank`` largest sustained displacements over all sensors, descending, and
    ``sustained_sensors`` the sensors they belong to."""

    pipe: str
    start: datetime
    end: datetime
    top_sensors: tuple[str, ...]
    initial: tuple[float, ...]
    remaining: tuple[float, ...]
    persistence: float | None
    sustained_sensors: tuple[str, ...]
    sustained: tuple[float, ...]


def _span_mean(
    values: npt.NDArray[np.float64],
    index: pd.DatetimeIndex,
    lo: datetime,
    hi: datetime,
    step: timedelta,
    pipe: str,
) -> npt.NDArray[np.float64]:
    """Mean per column over the windows with ``lo < t <= hi``; every window of the span must be
    present (``ValueError`` otherwise: the leak is too close to the edge of the data)."""
    first = int(index.searchsorted(lo, side="right"))
    last = int(index.searchsorted(hi, side="right"))
    expected = (hi - lo) // step
    if last - first != expected or expected < 1:
        raise ValueError(
            f"leak {pipe}: the span ({lo}, {hi}] needs {expected} windows, the data has "
            f"{last - first}"
        )
    mean: npt.NDArray[np.float64] = values[first:last].mean(axis=0)
    return mean


def leak_displacement(
    z: pd.DataFrame,
    leaks: pd.DataFrame,
    *,
    n_top: int,
    max_rank: int,
    baseline: timedelta,
    initial: timedelta,
    remaining_after: timedelta,
    sustained: timedelta,
) -> list[LeakDisplacement]:
    """The displacements of every abrupt leak of ``leaks`` (module docstring), in order of
    ``start``. ``z``: windows x sensors, float64, timezone-naive ``DatetimeIndex`` on a regular
    grid (the end of each window), columns in calibration order. ``leaks``: the columns of
    ``leaks_2018.csv`` with the dates parsed. All durations are required and positive, ``n_top``
    and ``max_rank`` between 1 and the number of sensors. ``ValueError`` if a span is not fully
    inside the data or if the leak is repaired (``end``) before the last window measured.
    """
    index = pd.DatetimeIndex(z.index)
    if index.tz is not None:
        raise ValueError("z must have a timezone-naive index (end of the 5-minute window)")
    if len(index) < 2:
        raise ValueError("z needs at least two windows")
    step = (index[1] - index[0]).to_pytimedelta()
    sensors = [str(s) for s in z.columns]
    for name, value in (("n_top", n_top), ("max_rank", max_rank)):
        if not 1 <= value <= len(sensors):
            raise ValueError(
                f"{name} must be between 1 and the {len(sensors)} sensors, got {value}"
            )
    durations: dict[str, timedelta] = {
        "baseline": baseline,
        "initial": initial,
        "remaining_after": remaining_after,
        "sustained": sustained,
    }
    for label, duration in durations.items():
        if duration <= timedelta(0):
            raise ValueError(f"{label} must be positive, got {duration}")
    values = z.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("z must be finite")
    abrupt = leaks[leaks["type"] == ABRUPT].sort_values("start", kind="stable")
    out = []
    for pipe, start_ts, end_ts in zip(abrupt["pipe"], abrupt["start"], abrupt["end"], strict=True):
        start = pd.Timestamp(start_ts).to_pydatetime()
        end = pd.Timestamp(end_ts).to_pydatetime()
        horizon = max(remaining_after + initial, sustained)
        if end < start + horizon:
            raise ValueError(
                f"leak {pipe} is repaired at {end}, before the last window measured "
                f"({start + horizon})"
            )
        name = str(pipe)
        pre = _span_mean(values, index, start - baseline, start, step, name)
        first = _span_mean(values, index, start, start + initial, step, name) - pre
        later = start + remaining_after
        left = _span_mean(values, index, later, later + initial, step, name) - pre
        held = _span_mean(values, index, start, start + sustained, step, name) - pre
        top = sorted(range(len(sensors)), key=lambda i: (-first[i], i))[:n_top]
        total = float(sum(first[i] for i in top))
        ranked = sorted(range(len(sensors)), key=lambda i: (-held[i], i))[:max_rank]
        out.append(
            LeakDisplacement(
                pipe=name,
                start=start,
                end=end,
                top_sensors=tuple(sensors[i] for i in top),
                initial=tuple(float(first[i]) for i in top),
                remaining=tuple(float(left[i]) for i in top),
                persistence=float(sum(left[i] for i in top)) / total if total > 0 else None,
                sustained_sensors=tuple(sensors[i] for i in ranked),
                sustained=tuple(float(held[i]) for i in ranked),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class Delta:
    """``delta_N``: the smallest, over the abrupt leaks, of the ``N``-th largest sustained
    displacement, and the leak it comes from (the first by ``start`` on a tie)."""

    rank: int
    delta: float
    weakest_pipe: str


def delta_by_rank(displacements: Sequence[LeakDisplacement]) -> list[Delta]:
    """``delta_N`` for ``N`` = 1 .. ``max_rank`` (the length of ``sustained``)."""
    if not displacements:
        raise ValueError("no abrupt leak to measure delta on")
    ranks = len(displacements[0].sustained)
    out = []
    for rank in range(1, ranks + 1):
        weakest = min(displacements, key=lambda d: d.sustained[rank - 1])
        out.append(Delta(rank, weakest.sustained[rank - 1], weakest.pipe))
    return out


@dataclass(frozen=True, slots=True)
class PersistenceVerdict:
    """The pre-registered acceptance of ADR-008 §G: ``median >= median_at_least`` and
    ``minimum >= minimum_at_least`` over the abrupt leaks. An undefined persistence counts as 0."""

    median: float
    minimum: float
    median_at_least: float
    minimum_at_least: float

    @property
    def passed(self) -> bool:
        return self.median >= self.median_at_least and self.minimum >= self.minimum_at_least


def persistence_verdict(
    displacements: Sequence[LeakDisplacement], *, median_at_least: float, minimum_at_least: float
) -> PersistenceVerdict:
    if not displacements:
        raise ValueError("no abrupt leak to test persistence on")
    ratios = [0.0 if d.persistence is None else d.persistence for d in displacements]
    return PersistenceVerdict(
        median=float(statistics.median(ratios)),
        minimum=min(ratios),
        median_at_least=median_at_least,
        minimum_at_least=minimum_at_least,
    )
