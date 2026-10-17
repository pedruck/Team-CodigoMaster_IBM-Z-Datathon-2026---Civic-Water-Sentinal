"""Out-of-fold residuals of a local hour-of-week baseline on 2018 (BE-012f, ADR-008 §A and §E).

Development stage of the calibration: until the ML owner delivers residuals (DEP-DET-05) the
detection owner needs a residual with a real structure, estimated without in-sample optimism.
The baseline predicts a window as the ``statistic`` of the same slot of the week (day of week,
``slot_minutes`` of the day) over the **other** calendar months; the 12 months are the folds.

Offline tool: needs pandas (dev dependency only) and is never imported by the server. Its
numbers are a development calibration (``provenance = dev-baseline-2018``, ``is_mock = true``),
never a delivered result, and it reads 2018 only.
"""

import gzip
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from sentinel.data.manifest import sha256
from sentinel.sensors import SensorRegistry

Statistic = Literal["median", "mean"]
MINUTES_PER_DAY = 1440
SCADA_2018_FILE = "scada_2018.csv.gz"
PRESSURE_PREFIX = "Pressure_"
RESIDUALS_FILE = "residuals.csv.gz"
FOLDS_FILE = "folds.json"
CSV_CHUNK_ROWS = 16_384
REDUCERS: dict[str, Callable[..., Any]] = {"median": np.median, "mean": np.mean}


@dataclass(frozen=True, slots=True, eq=False)
class OofResiduals:
    """Residuals ``observed - expected`` per window (index) and sensor (columns), with the fold
    (calendar month 1..12) each window was held out in. ``eq=False``: identity comparison,
    since DataFrames have no truth value."""

    residuals: pd.DataFrame
    fold: pd.Series
    slot_minutes: int
    statistic: Statistic


def _naive_datetime_index(index: pd.Index) -> pd.DatetimeIndex:
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError(f"the index must be a DatetimeIndex, got {type(index).__name__}")
    if index.tz is not None:
        raise ValueError("the index must be timezone-naive (end of the 5-minute window)")
    return index


def load_pressures_2018(data_dir: Path, registry: SensorRegistry) -> pd.DataFrame:
    """The 2018 pressure windows of data version v1, one column per registry pressure sensor.

    The sha256 of ``<data_dir>/v1/scada_2018.csv.gz`` is checked against ``MANIFEST.json``
    before the file is parsed (ADR-008 §A: data v1 is sealed). Columns are renamed from the
    SCADA names (``Pressure_n1``) to the registry IDs (``n1``) and follow the registry order;
    the index is the timezone-naive end of each 5-minute window. Raises ``ValueError`` for a
    hash that differs from the manifest, a registry pressure sensor missing from the file or a
    file pressure sensor missing from the registry. Only the 2018 file can be opened.
    """
    v1 = data_dir / "v1"
    manifest = json.loads((v1 / "MANIFEST.json").read_text(encoding="utf-8"))
    entry = manifest.get("files", {}).get(SCADA_2018_FILE)
    if entry is None:
        raise ValueError(f"MANIFEST.json of {v1} has no entry for {SCADA_2018_FILE}")
    actual = sha256(v1 / SCADA_2018_FILE)
    if actual != entry["sha256"]:
        raise ValueError(
            f"sha256 of {v1 / SCADA_2018_FILE} is {actual}, MANIFEST.json says {entry['sha256']}: "
            "the sealed data version changed (a change is a new version, docs/data.md section 9)"
        )
    scada = pd.read_csv(v1 / SCADA_2018_FILE, index_col="timestamp", parse_dates=True)
    _naive_datetime_index(scada.index)
    in_file = [
        str(column).removeprefix(PRESSURE_PREFIX)
        for column in scada.columns
        if str(column).startswith(PRESSURE_PREFIX)
    ]
    missing = [sensor for sensor in registry.pressure if sensor not in in_file]
    extra = [sensor for sensor in in_file if sensor not in registry.pressure]
    if missing:
        raise ValueError(f"registry pressure sensors missing from {SCADA_2018_FILE}: {missing}")
    if extra:
        raise ValueError(f"pressure sensors of {SCADA_2018_FILE} not in the registry: {extra}")
    pressures = scada[[PRESSURE_PREFIX + sensor for sensor in registry.pressure]]
    return pressures.set_axis(list(registry.pressure), axis="columns")


def oof_hour_of_week_residuals(
    pressures: pd.DataFrame, *, slot_minutes: int, statistic: Statistic
) -> OofResiduals:
    """Out-of-fold residuals ``observed - expected`` of an hour-of-week baseline.

    ``pressures``: one calendar year of windows (timezone-naive index = end of the window, as in
    the window contract), finite values, one column per sensor. The slot of a window is
    (day of week, minute of day // ``slot_minutes``) taken from its stamp as given, so with
    ``slot_minutes=60`` the stamps 00:00 to 00:55 share a slot. The folds are the calendar
    months: the ``expected`` of a window is the ``statistic`` (per sensor) of the same slot over
    the windows of the **other** months, never its own. ``slot_minutes`` must divide 1440 and
    nothing has a default (ADR-008 §B: no number without a provenance). Raises ``ValueError`` for
    an invalid ``slot_minutes`` or ``statistic``, a table that is not a naive-datetime, finite,
    single-year one, or a slot with no window outside the fold being held out.
    """
    if statistic not in REDUCERS:
        raise ValueError(f"statistic must be one of {sorted(REDUCERS)}, got {statistic!r}")
    if slot_minutes <= 0 or MINUTES_PER_DAY % slot_minutes != 0:
        raise ValueError(f"slot_minutes must be a positive divisor of 1440, got {slot_minutes}")
    index = _naive_datetime_index(pressures.index)
    years = sorted(set(index.year))
    if len(years) > 1:
        raise ValueError(f"the windows must lie within one calendar year, got years {years}")
    slots_per_day = MINUTES_PER_DAY // slot_minutes
    slot = np.asarray(
        index.dayofweek * slots_per_day + (index.hour * 60 + index.minute) // slot_minutes
    )
    month = np.asarray(index.month)
    values = pressures.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("pressures must be finite (no NaN or infinity)")
    reduce = REDUCERS[statistic]
    expected = np.empty_like(values)
    for slot_id in np.unique(slot):
        rows = np.flatnonzero(slot == slot_id)
        for fold_month in np.unique(month[rows]):
            held_out = rows[month[rows] == fold_month]
            training = rows[month[rows] != fold_month]
            if training.size == 0:
                weekday, k = divmod(int(slot_id), slots_per_day)
                raise ValueError(
                    f"no training data for the slot (weekday {weekday}, minute "
                    f"{k * slot_minutes} of the day) outside month {fold_month}"
                )
            expected[held_out] = reduce(values[training], axis=0)
    residuals = pd.DataFrame(values - expected, index=pressures.index, columns=pressures.columns)
    fold = pd.Series(month, index=pressures.index, name="fold")
    return OofResiduals(residuals, fold, slot_minutes, statistic)


def residual_series(oof: OofResiduals) -> dict[str, list[float]]:
    """Residuals per sensor, in column order, as ``calibrate(residuals=...)`` expects them."""
    return {str(sensor): oof.residuals[sensor].tolist() for sensor in oof.residuals.columns}


def _folds_bytes(oof: OofResiduals) -> bytes:
    """The fold definition: month -> [first, last window stamp], plus how the baseline was
    built. Canonical JSON (sorted keys, two-space indent, final newline)."""
    index = pd.DatetimeIndex(oof.residuals.index)
    fold = oof.fold.to_numpy()
    folds = {
        f"{month:02d}": [
            index[fold == month].min().isoformat(),
            index[fold == month].max().isoformat(),
        ]
        for month in sorted(set(fold.tolist()))
    }
    document = {"folds": folds, "slot_minutes": oof.slot_minutes, "statistic": oof.statistic}
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_oof(oof: OofResiduals, out_dir: Path) -> tuple[str, str]:
    """Write ``residuals.csv.gz`` and ``folds.json`` to ``out_dir`` (created if missing) and
    return ``(residuals_sha256, folds_sha256)``, the sha256 of the bytes written, ready for
    ``calibrate(residuals_sha256=..., folds_sha256=...)``.

    Byte-for-byte reproducible: gzip with ``mtime=0`` and no file name, floats as ``%.17g``
    (always recovers the same float64; read back with ``float_precision="round_trip"``),
    columns in table order, ``\\n`` line ends. The gzip bytes also depend on the zlib build.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    residuals_path = out_dir / RESIDUALS_FILE
    with (
        residuals_path.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
    ):
        for start in range(0, len(oof.residuals), CSV_CHUNK_ROWS):  # bounded memory
            text = oof.residuals.iloc[start : start + CSV_CHUNK_ROWS].to_csv(
                header=start == 0,
                index_label="timestamp",
                float_format="%.17g",
                date_format="%Y-%m-%d %H:%M:%S",
                lineterminator="\n",
            )
            compressed.write(text.encode("utf-8"))
    folds = _folds_bytes(oof)
    (out_dir / FOLDS_FILE).write_bytes(folds)
    return sha256(residuals_path), hashlib.sha256(folds).hexdigest()
