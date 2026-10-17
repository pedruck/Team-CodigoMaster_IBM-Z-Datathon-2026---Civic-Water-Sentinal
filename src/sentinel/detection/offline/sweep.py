"""Fast replay of the CUSUM detector over a ``z`` matrix and the parameter sweep (BE-012f).

``replay`` is the ``CusumDetector`` (ADR-007) without the contract around it, for sweeping
``h`` and ``N`` over a year of out-of-fold ``z`` (ADR-008 §C); ``sweep`` replays one grid of
``(h, N)`` and scores each configuration with the layer-1 metrics (``layer1``). Offline only: it
needs numpy and pandas (dev dependencies) and the server never imports it.
"""

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import numpy.typing as npt
import pandas as pd

from sentinel.detection.offline.layer1 import LEAK_TYPES, Layer1Result, MatchRule, evaluate


@dataclass(frozen=True, slots=True)
class Opening:
    """An alert opened at window ``index``; the fields are those of the detector's alert.

    ``timestamp``: the window's timestamp, i.e. the end of its interval (contract §1).
    ``sensors``: column indices of the unclaimed sensors above ``h``, ranked by ``(-S, index)``.
    ``statistic``: the N-th largest ``S`` among the unclaimed sensors (``> h`` by definition).
    ``suspected_onset``: the earliest, over ``sensors``, of the last window with ``S = 0``;
    ``None`` if one of them was never 0 since the first window.
    """

    index: int
    timestamp: datetime
    sensors: tuple[int, ...]
    statistic: float
    suspected_onset: datetime | None


def replay(
    z: npt.NDArray[np.float64],
    timestamps: Sequence[datetime] | pd.DatetimeIndex,
    *,
    k: float,
    h: float,
    n_min: int,
    c: float,
    h_close: float,
) -> list[Opening]:
    """The alerts ``CusumDetector.evaluate`` would open over ``z``, in window order.

    The arithmetic is the detector's, in the same order of operations, so every ``S`` and
    ``statistic`` is the same float64 (the parity test checks it against the detector).

    ``z`` is T x n float64 and finite, ``z = (expected - observed) / sigma`` with the columns
    in the calibration order. ``timestamps`` are the window timestamps, one per row, on a
    regular grid (a constant positive step): gaps are not supported here (the detector's decay
    across missing windows is not replayed), and the 2018 series has none. The parameters have
    no defaults and obey the constraints of the calibration artefact. ``ValueError`` otherwise.
    """
    n = _check_input(z, timestamps)
    _check_parameters(k=k, h=h, n_min=n_min, c=c, h_close=h_close, sensors=n)
    times = _timestamps(timestamps)
    cap = c * h
    z = np.ascontiguousarray(z)
    s = np.zeros(n)
    new = np.empty(n)
    claimed = np.zeros(n, dtype=np.bool_)
    any_claimed = False
    last_zero = np.full(n, -1, dtype=np.int64)  # -1: S was never 0
    openings: list[Opening] = []
    for t in range(len(z)):
        # S <- min(c*h, max(0, S + z - k)): the detector's order of operations.
        np.add(s, z[t], out=new)
        np.subtract(new, k, out=new)
        np.maximum(new, 0.0, out=new)
        np.minimum(new, cap, out=new)
        if any_claimed:
            # The detector releases a claimed sensor when min(S before, S now) < h_close. Without
            # gaps S before >= h_close always holds for a claimed sensor (it was above h >=
            # h_close when claimed and would have been released at the first S below h_close),
            # so S now decides.
            claimed &= new >= h_close
            any_claimed = bool(claimed.any())
        last_zero[new == 0.0] = t
        s, new = new, s
        if s.max() <= h:
            continue
        above = s > h
        if any_claimed:
            above &= ~claimed
        idx = np.flatnonzero(above)
        if idx.size < n_min:
            continue
        ranked = idx[np.argsort(-s[idx], kind="stable")]  # ties by index: idx is ascending
        zeros = last_zero[idx]
        earliest = int(zeros.min())
        openings.append(
            Opening(
                index=t,
                timestamp=times[t],
                sensors=tuple(int(i) for i in ranked),
                statistic=float(s[ranked[n_min - 1]]),
                suspected_onset=None if earliest < 0 else times[earliest],
            )
        )
        claimed[idx] = True
        any_claimed = True
    return openings


def _check_input(
    z: npt.NDArray[np.float64], timestamps: Sequence[datetime] | pd.DatetimeIndex
) -> int:
    """Validate ``z`` and the time grid; return the number of sensors."""
    if z.ndim != 2:
        raise ValueError(f"z must be a 2-D (windows x sensors) matrix, got {z.ndim}-D")
    if z.dtype != np.float64:
        raise ValueError(f"z must be float64 to match the detector bit for bit, got {z.dtype}")
    if z.shape[1] < 1:
        raise ValueError("z has no sensor column")
    if not np.isfinite(z).all():
        raise ValueError("z must be finite: the detector refuses non-finite values")
    if len(timestamps) != z.shape[0]:
        raise ValueError(f"z has {z.shape[0]} rows but there are {len(timestamps)} timestamps")
    if not _is_regular(timestamps):
        raise ValueError(
            "timestamps must be on a regular grid (constant positive step); gaps are not "
            "supported by the replay"
        )
    return int(z.shape[1])


def _is_regular(timestamps: Sequence[datetime] | pd.DatetimeIndex) -> bool:
    if isinstance(timestamps, pd.DatetimeIndex):
        if timestamps.hasnans:
            return False
        steps = timestamps[1:] - timestamps[:-1]  # elapsed time, whatever the time zone
        return bool(len(steps) == 0 or (steps[0] > pd.Timedelta(0) and (steps == steps[0]).all()))
    try:
        deltas = [b - a for a, b in itertools.pairwise(timestamps)]
    except TypeError:  # naive and aware datetimes mixed
        return False
    return not deltas or (deltas[0] > timedelta(0) and all(d == deltas[0] for d in deltas))


def _timestamps(timestamps: Sequence[datetime] | pd.DatetimeIndex) -> list[datetime]:
    if isinstance(timestamps, pd.DatetimeIndex):
        return list(timestamps.to_pydatetime())
    return list(timestamps)


def _check_parameters(
    *, k: float, h: float, n_min: int, c: float, h_close: float, sensors: int
) -> None:
    """The constraints of ``CalibrationArtefact``: a calibration the detector would refuse
    cannot be swept either."""
    for name, value in (("k", k), ("h", h), ("h_close", h_close)):
        if not (math.isfinite(value) and value > 0):
            raise ValueError(f"{name} must be positive and finite, got {value}")
    if not (math.isfinite(c) and c > 1):
        raise ValueError(f"c must be finite and above 1, got {c}")
    if h_close > h:
        raise ValueError(f"h_close ({h_close}) exceeds h ({h})")
    if not 1 <= n_min <= sensors:
        raise ValueError(f"n_min must be between 1 and the {sensors} sensors, got {n_min}")


def sweep(
    z: npt.NDArray[np.float64],
    timestamps: Sequence[datetime] | pd.DatetimeIndex,
    leaks: pd.DataFrame,
    *,
    k: float,
    h_values: Sequence[float],
    n_values: Sequence[int],
    c: float,
    h_close: float,
    match: MatchRule,
) -> pd.DataFrame:
    """Layer-1 metrics (ADR-008 §F) of every ``(h, N)`` of the grid; one row per pair, ``h``
    major in the order given, ``N`` minor. Deterministic: the same arguments give the same frame.

    ``z``, ``timestamps`` and the fixed ``k``, ``c``, ``h_close`` are those of ``replay``;
    ``leaks`` and ``match`` those of ``layer1.evaluate`` (``match`` is PROVISIONAL, Q-UI-06);
    the evaluated period is ``len(z)`` windows of one step. Every pair must be a calibration the
    detector would accept (so every ``h >= h_close``), else ``ValueError`` before any replay.

    Columns: ``h``, ``n_min``; ``openings``, ``false_alarms``, ``false_alarms_per_week``,
    ``repeat_openings`` (see ``Layer1Result``); ``detected`` and ``total`` leaks; per leak type
    ``detected_<type>``, ``total_<type>``, ``missed_<type>`` (tuple of pipes) and
    ``median_delay_start_<type>`` / ``median_delay_peak_<type>`` over the detected leaks of that
    type (``NaT`` if none; the peak delay can be negative). Counts and medians only, no
    confidence intervals (ADR-008 §F). Note that the false alarms are not monotone in ``h`` in
    general (see the tests): read the table, do not interpolate it.
    """
    sensors = _check_input(z, timestamps)
    if len(timestamps) < 2:
        raise ValueError("at least two timestamps are needed to know the step and the period")
    for name, values in (("h_values", h_values), ("n_values", n_values)):
        if len(values) == 0:
            raise ValueError(f"{name} is empty")
        if len(set(values)) != len(values):
            raise ValueError(f"{name} has repeated values: {list(values)}")
    for h, n_min in itertools.product(h_values, n_values):
        _check_parameters(k=k, h=h, n_min=n_min, c=c, h_close=h_close, sensors=sensors)
    period = _step(timestamps) * len(z)
    # The leaks, the rule and their time zone fit the timestamps, before replaying anything.
    evaluate([timestamps[0]], leaks, period=period, match=match)
    rows = []
    for h, n_min in itertools.product(h_values, n_values):
        openings = replay(z, timestamps, k=k, h=h, n_min=n_min, c=c, h_close=h_close)
        result = evaluate([o.timestamp for o in openings], leaks, period=period, match=match)
        rows.append(_row(h, n_min, result))
    table = pd.DataFrame(rows)
    for column in table.columns:
        if column.startswith("median_delay_"):  # a column without any delay would be datetime64
            table[column] = pd.to_timedelta([row[column] for row in rows])
    return table


def _step(timestamps: Sequence[datetime] | pd.DatetimeIndex) -> timedelta:
    if isinstance(timestamps, pd.DatetimeIndex):
        return (timestamps[1] - timestamps[0]).to_pytimedelta()
    return timestamps[1] - timestamps[0]


def _row(h: float, n_min: int, result: Layer1Result) -> dict[str, object]:
    row: dict[str, object] = {
        "h": h,
        "n_min": n_min,
        "openings": result.openings,
        "false_alarms": result.false_alarms,
        "false_alarms_per_week": result.false_alarms_per_week,
        "repeat_openings": result.repeat_openings,
        "detected": result.detected,
        "total": result.total,
    }
    for kind in LEAK_TYPES:
        count = result.by_type[kind]
        found = [leak for leak in result.leaks if leak.type == kind and leak.detected]
        row[f"detected_{kind}"] = count.detected
        row[f"total_{kind}"] = count.total
        row[f"missed_{kind}"] = count.missed
        row[f"median_delay_start_{kind}"] = _median([leak.delay_from_start for leak in found])
        row[f"median_delay_peak_{kind}"] = _median([leak.delay_from_peak for leak in found])
    return row


def _median(delays: Sequence[timedelta | None]) -> timedelta | None:
    """The median of the known delays (the mean of the two middle ones if even), else ``None``."""
    known = sorted(delay for delay in delays if delay is not None)
    if not known:
        return None
    middle = len(known) // 2
    return known[middle] if len(known) % 2 else (known[middle - 1] + known[middle]) / 2
