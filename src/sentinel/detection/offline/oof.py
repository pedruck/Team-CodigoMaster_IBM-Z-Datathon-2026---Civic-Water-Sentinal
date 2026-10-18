"""Reader of the ML owner's out-of-fold 2018 residuals for the final calibration (BE-012j).

The ML owner writes them per forecaster variant, outside git (DEP-DET-05,
``sentinel.forecasting.oof``, in ``$SENTINEL_DATA_DIR/v1/oof/<model>/<variant>/``):

- ``residuals_long.csv.gz``: ``timestamp, sensor, observed, expected, fold``, one row per window
  and pressure sensor, by timestamp then registry order;
- ``folds.json``: calendar month -> first and last window stamp;
- ``declaration.json``: the forecaster identity and the sha256 of the two files above (of the
  **uncompressed** CSV text for the residuals, of the file bytes for the folds).

``load_oof_residuals`` checks all of that before returning the residuals in wide form (one column
per sensor, registry order). ``z_matrix`` turns them into the detector's ``z``; the two
statistics (``quantization_step``, ``lag1_autocorrelation``) are what the calibration needs to
fix the sigma floor and the bounds of the ``h`` grid (ADR-008 §C). Offline only (numpy, pandas);
the server never imports it. Only 2018 is read: any path with ``2019`` is refused (ADR-008 §A).
"""

import gzip
import hashlib
import io
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from sentinel.detection.calibration import ForecasterIdentity
from sentinel.sensors import SensorRegistry

LONG_FILE = "residuals_long.csv.gz"
FOLDS_FILE = "folds.json"
DECLARATION_FILE = "declaration.json"
COLUMNS = ("timestamp", "sensor", "observed", "expected", "fold")
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
YEAR = 2018
WINDOW = pd.Timedelta(minutes=5)  # contract §1
SEALED = "2019"


def refuse_2019(*paths: Path) -> None:
    """``ValueError`` for any path that names 2019: the 2019 files are sealed for the detection
    owner (ADR-008 §A). The path is checked as given and resolved (a symlink cannot hide it)."""
    for path in paths:
        for text in (str(path), str(path.expanduser().resolve())):
            if SEALED in text:
                raise ValueError(
                    f"refusing {path}: no 2019 file is opened for detection work (ADR-008 §A)"
                )


@dataclass(frozen=True, slots=True, eq=False)
class OofData:
    """Out-of-fold observed and expected pressures, wide (windows x sensors, registry order).

    The index is the timezone-naive end of each 5-minute window (contract §1); ``fold`` is the
    calendar month each window was held out in. ``residuals_sha256`` is the sha256 of the
    uncompressed CSV text, ``folds_sha256`` that of ``folds.json``: the two hashes of the
    calibration artefact. ``eq=False``: identity comparison (DataFrames have no truth value).
    """

    observed: pd.DataFrame
    expected: pd.DataFrame
    fold: pd.Series
    forecaster: ForecasterIdentity
    residuals_sha256: str
    folds_sha256: str

    @property
    def residuals(self) -> pd.DataFrame:
        """``observed - expected`` in metres, as the detector computes it."""
        return self.observed - self.expected


def _declared(declaration: Mapping[str, Any], *keys: str) -> Any:
    value: Any = declaration
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            raise ValueError(f"{DECLARATION_FILE} lacks {'.'.join(keys)}")
        value = value[key]
    return value


def load_oof_residuals(directory: Path, registry: SensorRegistry) -> OofData:
    """Read and check one variant folder of DEP-DET-05 (see the module docstring).

    ``ValueError``, before any residual is returned, for: a path with ``2019``; a hash that
    differs from ``declaration.json``; columns other than ``timestamp, sensor, observed,
    expected, fold``; a window whose sensors are not exactly the registry's pressure sensors in
    registry order (one missing, extra or out of order); a time grid that is not a regular
    5-minute grid; a window outside 2018; a non-finite value; a window without a fold, with
    different folds per sensor or outside the months of ``folds.json``.
    """
    refuse_2019(directory)
    declaration = json.loads((directory / DECLARATION_FILE).read_text(encoding="utf-8"))
    year = _declared(declaration, "year")
    if year != YEAR:
        raise ValueError(f"{DECLARATION_FILE} declares year {year}, expected {YEAR}")
    forecaster = ForecasterIdentity.model_validate(_declared(declaration, "forecaster"))

    text = gzip.decompress((directory / LONG_FILE).read_bytes())
    residuals_sha256 = hashlib.sha256(text).hexdigest()
    declared = _declared(declaration, "files", LONG_FILE, "sha256_of_csv_text")
    if residuals_sha256 != declared:
        raise ValueError(
            f"sha256 of the {LONG_FILE} text is {residuals_sha256}, {DECLARATION_FILE} says "
            f"{declared}"
        )
    folds_bytes = (directory / FOLDS_FILE).read_bytes()
    folds_sha256 = hashlib.sha256(folds_bytes).hexdigest()
    declared = _declared(declaration, "files", FOLDS_FILE, "sha256")
    if folds_sha256 != declared:
        raise ValueError(
            f"sha256 of {FOLDS_FILE} is {folds_sha256}, {DECLARATION_FILE} says {declared}"
        )

    table = pd.read_csv(
        io.BytesIO(text), dtype={"sensor": str}, float_precision="round_trip", na_filter=True
    )
    if tuple(table.columns) != COLUMNS:
        raise ValueError(f"{LONG_FILE} columns are {list(table.columns)}, expected {COLUMNS}")
    sensors = registry.pressure
    n = len(sensors)
    if len(table) == 0 or len(table) % n:
        raise ValueError(
            f"{LONG_FILE} has {len(table)} rows, not a multiple of the {n} pressure sensors: a "
            "sensor is missing in some window"
        )
    names = table["sensor"].to_numpy(dtype=object).reshape(-1, n)
    wrong = np.flatnonzero((names != np.asarray(sensors, dtype=object)).any(axis=1))
    if wrong.size:
        row = int(wrong[0])
        raise ValueError(
            f"window {row} of {LONG_FILE} lists the sensors {list(names[row])}, not the registry's "
            "pressure sensors in registry order (a sensor missing, extra or out of order)"
        )
    stamps = pd.to_datetime(table["timestamp"], format=TIMESTAMP_FORMAT).to_numpy().reshape(-1, n)
    if (stamps != stamps[:, :1]).any():
        raise ValueError(f"{LONG_FILE}: the rows of a window do not share one timestamp")
    index = pd.DatetimeIndex(stamps[:, 0])
    _check_grid(index)
    observed = _wide(table["observed"], n, "observed")
    expected = _wide(table["expected"], n, "expected")
    fold = _folds(table["fold"], n, index, json.loads(folds_bytes))
    columns = list(sensors)
    return OofData(
        observed=pd.DataFrame(observed, index=index, columns=columns),
        expected=pd.DataFrame(expected, index=index, columns=columns),
        fold=pd.Series(fold, index=index, name="fold"),
        forecaster=forecaster,
        residuals_sha256=residuals_sha256,
        folds_sha256=folds_sha256,
    )


def _check_grid(index: pd.DatetimeIndex) -> None:
    years = sorted(set(index.year))
    if years != [YEAR]:
        raise ValueError(f"the windows must all lie in {YEAR}, got years {years}")
    steps = index[1:] - index[:-1]
    if len(index) < 2 or not (steps == WINDOW).all():
        raise ValueError(
            "the windows must be on a regular 5-minute grid (no gap, no repeat, in order)"
        )


def _wide(column: pd.Series, n: int, name: str) -> npt.NDArray[np.float64]:
    values = column.to_numpy(dtype=np.float64).reshape(-1, n)
    if not np.isfinite(values).all():
        raise ValueError(f"non-finite {name} value in {LONG_FILE}")
    return values


def _folds(
    column: pd.Series, n: int, index: pd.DatetimeIndex, document: Mapping[str, Any]
) -> npt.NDArray[np.int64]:
    raw = column.to_numpy(dtype=np.float64).reshape(-1, n)
    if not np.isfinite(raw).all():
        raise ValueError(f"a window of {LONG_FILE} has no fold")
    if (raw != raw[:, :1]).any():
        raise ValueError(f"the rows of a window of {LONG_FILE} have different folds")
    fold = raw[:, 0]
    if (fold != np.round(fold)).any():
        raise ValueError(f"{LONG_FILE} has a fold that is not an integer")
    months = fold.astype(np.int64)
    spans = _declared(document, "folds")
    for month in sorted(set(months.tolist())):
        span = spans.get(f"{month:02d}")
        if span is None:
            raise ValueError(f"fold {month} is not in {FOLDS_FILE}")
        first, last = (datetime.fromisoformat(t) for t in span)
        inside = index[months == month]
        if inside.min() < first or inside.max() > last:
            raise ValueError(f"windows of fold {month} lie outside its span {span} in {FOLDS_FILE}")
    return months


def z_matrix(residuals: pd.DataFrame, sigma: Mapping[str, float]) -> npt.NDArray[np.float64]:
    """``z = (expected - observed) / sigma`` as float64 (windows x sensors), with the detector's
    arithmetic (``-residual / sigma``, contract §3) so the replay matches it bit for bit. The
    columns of ``residuals`` must be the keys of ``sigma`` in the same order."""
    if list(residuals.columns) != list(sigma):
        raise ValueError(
            "the residual columns and the sigma sensors differ or are in another order"
        )
    values = residuals.to_numpy(dtype=np.float64)
    scale = np.array([sigma[s] for s in residuals.columns], dtype=np.float64)
    return np.ascontiguousarray(np.negative(values) / scale)


def quantization_step(observed: pd.DataFrame, *, decimals: int) -> pd.Series:
    """Per sensor, the smallest non-zero difference between distinct observed values: the
    resolution of the measurement. The differences are rounded to ``decimals`` (the precision
    the file was written with) so float noise does not create a smaller step. ``ValueError``
    for a sensor with fewer than two distinct values."""
    steps = {}
    for sensor in observed.columns:
        values = np.unique(observed[sensor].to_numpy(dtype=np.float64))
        if values.size < 2:
            raise ValueError(f"sensor {sensor!r} has fewer than two distinct observed values")
        gaps = np.round(np.diff(values), decimals)
        steps[sensor] = float(gaps[gaps > 0].min())
    return pd.Series(steps, name="quantization_step_m")


def lag1_autocorrelation(residuals: pd.DataFrame) -> pd.Series:
    """Lag-1 autocorrelation of each sensor's residual over consecutive windows:
    ``sum((x_t - m)(x_{t+1} - m)) / sum((x_t - m)^2)``, ``m`` the sensor's mean. ~0 for white
    noise, the coefficient for an AR(1). ``ValueError`` for a constant series."""
    values = residuals.to_numpy(dtype=np.float64)
    if len(values) < 2:
        raise ValueError("at least two windows are needed for a lag-1 autocorrelation")
    centred = values - values.mean(axis=0)
    denominator = (centred * centred).sum(axis=0)
    flat = [str(c) for c, d in zip(residuals.columns, denominator, strict=True) if d == 0]
    if flat:
        raise ValueError(f"lag-1 autocorrelation undefined for constant residuals: {flat}")
    numerator = (centred[:-1] * centred[1:]).sum(axis=0)
    return pd.Series(numerator / denominator, index=residuals.columns, name="lag1_autocorr")


def inflation_factor(phi: float) -> float:
    """``(1 + phi) / (1 - phi)``: how much an AR(1) with lag-1 coefficient ``phi`` inflates the
    variance of a long sum against white noise; infinite for ``phi >= 1``. It bounds the ``h``
    grid (the CUSUM accumulates sums of ``z``), never chooses a value in it."""
    return math.inf if phi >= 1 else (1 + phi) / (1 - phi)
