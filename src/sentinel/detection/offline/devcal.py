"""Development calibration of the CUSUM detector on 2018 (BE-012f, ADR-008 §C, §E and §H).

One reproducible pipeline: the 2018 pressures of data v1 -> out-of-fold residuals of the
hour-of-week baseline (``baseline``) -> the calibration artefact ``dev-baseline-2018``
(``calibration.calibrate``) -> ``z = (expected - observed) / sigma = -residual / sigma`` -> the
``(h, N)`` sweep against the 2018 labels (``sweep``) -> a report. Besides the artefact it writes
the structure of the residuals (autocorrelation, diurnal heteroscedasticity, cross-sensor
correlation, extreme single-window ``z``) and how sigma changes when the windows with a labelled
leak are masked (ADR-008 §C, §I): the facts the final calibration has to respect.

Every number is a development choice: ``run`` and the command line take all of them as arguments
and have no defaults for any (ADR-008 §B). The outputs are labelled ``dev-baseline-2018`` and
``is_mock = true``, never a delivered result, and no 2019 file is opened (ADR-008 §A). Offline
only: needs numpy and pandas (dev dependencies) and the server never imports it.

Command line (all arguments are required)::

    python -m sentinel.detection.offline.devcal --data-dir D --out-dir O --slot-minutes 60 \\
        --statistic median --sigma-floor-m 0.01 --k 0.5 --h 4 8 --n 1 2 --c 2 \\
        --h-close-ratio 0.5 --artefact-h 8 --artefact-n 2 \\
        --max-delay-abrupt-h 48 --max-delay-incipient-h 720
"""

import argparse
import json
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from sentinel.data.clean_mask import carregar_vazamentos, mascara_limpa
from sentinel.data.manifest import sha256
from sentinel.detection.calibration import (
    MAD_TO_SIGMA,
    CalibrationArtefact,
    ForecasterIdentity,
    Provenance,
    calibrate,
    write_artefact,
)
from sentinel.detection.offline.baseline import (
    SCADA_2018_FILE,
    Statistic,
    load_pressures_2018,
    oof_hour_of_week_residuals,
    residual_series,
    write_oof,
)
from sentinel.detection.offline.layer1 import LEAK_TYPES, MatchRule
from sentinel.detection.offline.sweep import sweep
from sentinel.sensors import SensorRegistry

# The official registry shipped with the repository (src/sentinel/detection/offline -> repo root).
OFFICIAL_REGISTRY_PATH = Path(__file__).resolve().parents[4] / "config" / "sensors.battledim.json"

PROVENANCE: Provenance = "dev-baseline-2018"
FORECASTER_NAME = "p4-dev-hour-of-week"
FORECASTER_VERSION_LIMIT = 32  # CalibrationArtefact / ForecasterIdentity
LEAKS_2018_FILE = "leaks_2018.csv"
ARTEFACT_FILE = f"calibration.{PROVENANCE}.json"
SWEEP_FILE = "sweep.csv"
REPORT_FILE = "report.md"

# Mask policies of ``sentinel.data.clean_mask`` used for the sensitivity of sigma. ``todos``
# excludes every window inside [start, end] of any labelled leak (repaired or not), i.e. the
# windows with an active leak: it is the policy asked for by ADR-008 §C. ``reparados`` (the P1
# default) excludes only the repaired leaks; it is reported next to it because ``todos`` leaves
# only a few days of the year and so also changes the period, not only the leaks.
MASK_POLICIES = ("todos", "reparados")
PRIMARY_MASK_POLICY = MASK_POLICIES[0]

# |z| above which a single-window z counts as extreme in the structure report (ADR-008 §I
# measures "the rate of extreme single-window residuals"). A reporting diagnostic, not a
# calibrated parameter: nothing in the detector or the sweep uses it.
EXTREME_Z_THRESHOLD = 4.0

NOTICE = (
    "simulated benchmark (BattLeDIM L-Town); thresholds fixed on 2018; development "
    "calibration (`dev-baseline-2018`, is_mock), not a delivered result; no 2019 file "
    "opened; matching rule provisional (Q-UI-06); δ/k not pre-registered"
)
NOT_PREREGISTERED = "development choice, not pre-registered"

STRUCTURE_COLUMNS = ("lag1_autocorr", "diurnal_scale_ratio", "common_mode_corr", "extreme_rate")


# --- Residual structure ---------------------------------------------------------------------


def _finite_values(table: pd.DataFrame) -> npt.NDArray[np.float64]:
    values = table.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("the residuals must be finite (no NaN or infinity)")
    return values


def _require_spread(values: npt.NDArray[np.float64], columns: pd.Index, what: str) -> None:
    flat = [
        str(c) for c, spread in zip(columns, np.ptp(values, axis=0), strict=True) if spread == 0
    ]
    if flat:
        raise ValueError(f"{what} is undefined for sensors with constant residuals: {flat}")


def lag1_autocorrelation(residuals: pd.DataFrame) -> pd.Series:
    """Lag-1 autocorrelation of each sensor's residual (columns), over consecutive rows.

    ``sum((x_t - m) (x_{t+1} - m)) / sum((x_t - m)^2)``, ``m`` the mean of the sensor. White
    noise gives ~0 and an AR(1) series its coefficient. A sensor with constant residuals has
    none: ``ValueError`` naming it.
    """
    values = _finite_values(residuals)
    if len(values) < 2:
        raise ValueError("at least two windows are needed for a lag-1 autocorrelation")
    _require_spread(values, residuals.columns, "the autocorrelation")
    centred = values - values.mean(axis=0)
    lagged = (centred[:-1] * centred[1:]).sum(axis=0)
    variance = (centred * centred).sum(axis=0)
    return pd.Series(lagged / variance, index=residuals.columns, name="lag1_autocorr")


def diurnal_robust_scale(residuals: pd.DataFrame) -> pd.DataFrame:
    """Robust scale ``1.4826 * MAD`` of each sensor per hour of the day (the heteroscedasticity
    a single sigma per sensor ignores): rows are the hours present in the data (``hour``, taken
    from the stamp as given, which is the end of the 5-minute window), columns the sensors. The
    MAD is taken around the median of each hour, as ``calibrate`` does over the whole series."""
    if not isinstance(residuals.index, pd.DatetimeIndex):
        raise ValueError("the index must be a DatetimeIndex to group the windows by hour")
    values = _finite_values(residuals)
    hours = np.asarray(residuals.index.hour)
    present = sorted(int(h) for h in np.unique(hours))
    scales = []
    for hour in present:
        block = values[hours == hour]
        centre = np.median(block, axis=0)
        scales.append(MAD_TO_SIGMA * np.median(np.abs(block - centre), axis=0))
    return pd.DataFrame(
        np.vstack(scales), index=pd.Index(present, name="hour"), columns=residuals.columns
    )


def diurnal_scale_ratio(scale: pd.DataFrame, *, sigma_floor_m: float) -> pd.Series:
    """Largest over smallest hourly scale per sensor (1 = no diurnal heteroscedasticity), each
    scale floored at ``sigma_floor_m`` as sigma is: a quantised sensor can have a MAD of 0 in
    some hour, which would make the ratio infinite."""
    _check_positive_finite("sigma_floor_m", sigma_floor_m)
    floored = scale.clip(lower=sigma_floor_m)
    return (floored.max(axis=0) / floored.min(axis=0)).rename("diurnal_scale_ratio")


@dataclass(frozen=True, slots=True)
class PairwiseCorrelation:
    """Correlation between the residuals of two different sensors, over all ``pairs`` of them:
    the median, the largest (signed) value and the pair that has it."""

    median: float
    maximum: float
    maximum_pair: tuple[str, str]
    pairs: int


def pairwise_correlation(residuals: pd.DataFrame) -> PairwiseCorrelation:
    """Median and maximum of the Pearson correlations over all sensor pairs."""
    values = _finite_values(residuals)
    if values.shape[1] < 2 or len(values) < 2:
        raise ValueError("at least two sensors and two windows are needed for a correlation")
    _require_spread(values, residuals.columns, "the correlation")
    corr = np.asarray(np.corrcoef(values, rowvar=False), dtype=np.float64)
    rows, cols = np.triu_indices(values.shape[1], k=1)
    pair_values = corr[rows, cols]
    best = int(np.argmax(pair_values))
    names = [str(c) for c in residuals.columns]
    return PairwiseCorrelation(
        median=float(np.median(pair_values)),
        maximum=float(pair_values[best]),
        maximum_pair=(names[rows[best]], names[cols[best]]),
        pairs=int(pair_values.size),
    )


def common_mode_correlation(residuals: pd.DataFrame) -> pd.Series:
    """Correlation of each sensor's residual with the **common mode**, the median across
    sensors of each window (the sensor itself is part of that median)."""
    values = _finite_values(residuals)
    _require_spread(values, residuals.columns, "the common-mode correlation")
    common = np.median(values, axis=1)
    if np.ptp(common) == 0:
        raise ValueError("the common mode (median across sensors) is constant")
    common_c = common - common.mean()
    centred = values - values.mean(axis=0)
    corr = (centred * common_c[:, None]).sum(axis=0) / np.sqrt(
        (centred**2).sum(axis=0) * (common_c**2).sum()
    )
    return pd.Series(corr, index=residuals.columns, name="common_mode_corr")


def _check_positive_finite(name: str, value: float) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be positive and finite, got {value}")


def extreme_rate(z: pd.DataFrame, *, threshold: float) -> pd.Series:
    """Fraction of windows of each sensor with ``|z|`` strictly above ``threshold``."""
    _check_positive_finite("threshold", threshold)
    values = _finite_values(z)
    return pd.Series(
        (np.abs(values) > threshold).mean(axis=0), index=z.columns, name="extreme_rate"
    )


def gaussian_extreme_rate(threshold: float) -> float:
    """``P(|Z| > threshold)`` for a standard normal ``Z``: the reference of ``extreme_rate`` if
    ``z`` were independent standard normal (the real ``z`` is not, ADR-008 Context)."""
    _check_positive_finite("threshold", threshold)
    return math.erfc(threshold / math.sqrt(2.0))


def range_summary(structure: pd.DataFrame) -> pd.DataFrame:
    """Per statistic (column of ``structure``, one row per sensor): the ``min``, ``median`` and
    ``max`` across sensors and the sensors that hold the extremes."""
    rows = {
        str(statistic): {
            "min": float(structure[statistic].min()),
            "median": float(structure[statistic].median()),
            "max": float(structure[statistic].max()),
            "min_sensor": str(structure[statistic].idxmin()),
            "max_sensor": str(structure[statistic].idxmax()),
        }
        for statistic in structure.columns
    }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("statistic")


# --- Sensitivity of sigma to the leak labels -----------------------------------------------


def _robust_sigma(values: npt.NDArray[np.float64], floor: float) -> npt.NDArray[np.float64]:
    """``max(floor, 1.4826 * MAD)`` per column: the estimator of ``calibrate``."""
    centre = np.median(values, axis=0)
    return np.maximum(floor, MAD_TO_SIGMA * np.median(np.abs(values - centre), axis=0))


def sigma_sensitivity(
    residuals: pd.DataFrame, clean: pd.Series, *, sigma_floor_m: float
) -> pd.DataFrame:
    """Sigma per sensor from all the windows and from the clean ones only (ADR-008 §C).

    ``clean``: boolean mask over the windows of ``residuals`` (``True`` = no active leak, as
    ``mascara_limpa`` gives it). Columns: ``sigma_all``, ``sigma_clean`` (both
    ``max(sigma_floor_m, 1.4826 * MAD)``) and ``ratio = sigma_all / sigma_clean``; the
    ``calibrate`` estimator. ``ValueError`` for a floor that is not positive and finite, a mask
    that is not over the same windows or one with no clean window.
    """
    _check_positive_finite("sigma_floor_m", sigma_floor_m)
    if not clean.index.equals(residuals.index):
        raise ValueError("the mask must cover the same windows as the residuals (equal index)")
    keep = clean.to_numpy(dtype=bool)
    if not keep.any():
        raise ValueError("no clean window: the mask excludes every window")
    values = _finite_values(residuals)
    sigma_all = _robust_sigma(values, sigma_floor_m)
    sigma_clean = _robust_sigma(values[keep], sigma_floor_m)
    return pd.DataFrame(
        {"sigma_all": sigma_all, "sigma_clean": sigma_clean, "ratio": sigma_all / sigma_clean},
        index=residuals.columns,
    ).rename_axis("sensor")


# --- The sweep, summarised -----------------------------------------------------------------


def _fraction(table: pd.DataFrame, kind: str) -> list[str]:
    return [
        f"{d}/{t}" for d, t in zip(table[f"detected_{kind}"], table[f"total_{kind}"], strict=True)
    ]


def _hours(delays: pd.Series) -> pd.Series:
    hours: pd.Series = delays.dt.total_seconds() / 3600.0
    return hours


def summarise_sweep(table: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The three views of a ``sweep`` table used by the report: ``counts`` (detected n/N per
    leak type and in total, openings, false alarms, per week, repeats), ``delays_h`` (median
    delay in hours from the label start and from the peak, per type) and ``missed`` (the
    pipes of the missed leaks). One row per ``(h, N)``, in the order of ``table``."""
    key = pd.DataFrame({"h": table["h"], "N": table["n_min"]})
    counts = key.assign(
        abrupt=_fraction(table, "abrupt"),
        incipient=_fraction(table, "incipient"),
        all=[f"{d}/{t}" for d, t in zip(table["detected"], table["total"], strict=True)],
        openings=table["openings"],
        false_alarms=table["false_alarms"],
        false_alarms_per_week=table["false_alarms_per_week"],
        repeat_openings=table["repeat_openings"],
    )
    delays = key.copy()
    for origin in ("start", "peak"):
        for kind in LEAK_TYPES:
            delays[f"{origin}_{kind}"] = _hours(table[f"median_delay_{origin}_{kind}"])
    missed = key.assign(
        **{
            kind: [", ".join(pipes) or "-" for pipes in table[f"missed_{kind}"]]
            for kind in LEAK_TYPES
        }
    )
    return {"counts": counts, "delays_h": delays, "missed": missed}


# --- The pipeline --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DevCalParameters:
    """Every number of a run; all of them are development choices (``NOT_PREREGISTERED``)."""

    slot_minutes: int
    statistic: Statistic
    sigma_floor_m: float
    k: float
    h_values: tuple[float, ...]
    n_values: tuple[int, ...]
    c: float
    h_close_ratio: float
    artefact_h: float
    artefact_n: int
    match: MatchRule


@dataclass(frozen=True, slots=True, eq=False)
class DevCalReport:
    """What ``run`` wrote and measured. The tables are those printed in ``report.md``.

    ``structure`` has one row per sensor (``STRUCTURE_COLUMNS``) and ``structure_ranges`` its
    min / median / max across sensors; ``pairwise`` and ``diurnal_scale`` (hour x sensor) complete
    them. ``sigma_sensitivity`` and ``clean_fraction`` are keyed by mask policy
    (``MASK_POLICIES``). ``sweep`` is the ``sweep.sweep`` table of every ``(h, N)`` (``h`` major).
    ``elapsed_seconds`` is wall-clock time and is kept out of ``report.md``, which is reproducible.
    """

    out_dir: Path
    residuals_path: Path
    folds_path: Path
    artefact_path: Path
    sweep_path: Path
    report_path: Path
    residuals_sha256: str
    folds_sha256: str
    artefact_sha256: str
    scada_sha256: str
    leaks_sha256: str
    parameters: DevCalParameters
    artefact: CalibrationArtefact
    structure: pd.DataFrame
    structure_ranges: pd.DataFrame
    pairwise: PairwiseCorrelation
    diurnal_scale: pd.DataFrame
    sigma_sensitivity: dict[str, pd.DataFrame]
    clean_fraction: dict[str, float]
    sweep: pd.DataFrame
    elapsed_seconds: float


def forecaster_identity(*, slot_minutes: int, statistic: str) -> ForecasterIdentity:
    """The identity the artefact carries: the baseline is a local stand-in, not a forecaster."""
    version = f"0.1.0+s{slot_minutes}-{statistic}"[:FORECASTER_VERSION_LIMIT]
    return ForecasterIdentity(name=FORECASTER_NAME, version=version)


def _check_arguments(parameters: DevCalParameters) -> None:
    """Cheap checks before anything is read or written."""
    ratio = parameters.h_close_ratio
    if not (math.isfinite(ratio) and 0 < ratio <= 1):
        raise ValueError(f"h_close_ratio must be in (0, 1], got {ratio}")
    for name, values in (("h_values", parameters.h_values), ("n_values", parameters.n_values)):
        if len(values) == 0:
            raise ValueError(f"{name} is empty")
    if parameters.artefact_h not in parameters.h_values:
        raise ValueError(
            f"artefact_h ({parameters.artefact_h}) must be one of h_values {parameters.h_values}, "
            "so the report shows what the artefact does"
        )
    if parameters.artefact_n not in parameters.n_values:
        raise ValueError(
            f"artefact_n ({parameters.artefact_n}) must be one of n_values {parameters.n_values}, "
            "so the report shows what the artefact does"
        )


def _load_leaks(data_dir: Path) -> tuple[pd.DataFrame, str]:
    """``leaks_2018.csv`` after checking it against ``MANIFEST.json`` (data v1 is sealed)."""
    v1 = data_dir / "v1"
    manifest = json.loads((v1 / "MANIFEST.json").read_text(encoding="utf-8"))
    entry = manifest.get("files", {}).get(LEAKS_2018_FILE)
    if entry is None:
        raise ValueError(f"MANIFEST.json of {v1} has no entry for {LEAKS_2018_FILE}")
    actual = sha256(v1 / LEAKS_2018_FILE)
    if actual != entry["sha256"]:
        raise ValueError(
            f"sha256 of {v1 / LEAKS_2018_FILE} is {actual}, MANIFEST.json says {entry['sha256']}: "
            "the sealed data version changed (a change is a new version, docs/data.md section 9)"
        )
    return carregar_vazamentos(str(data_dir)), actual


def _scada_sha256(data_dir: Path) -> str:
    manifest = json.loads((data_dir / "v1" / "MANIFEST.json").read_text(encoding="utf-8"))
    return str(manifest["files"][SCADA_2018_FILE]["sha256"])


def run(
    data_dir: Path,
    out_dir: Path,
    *,
    slot_minutes: int,
    statistic: Statistic,
    sigma_floor_m: float,
    k: float,
    h_values: Sequence[float],
    n_values: Sequence[int],
    c: float,
    h_close_ratio: float,
    artefact_h: float,
    artefact_n: int,
    match: MatchRule,
    registry_path: Path = OFFICIAL_REGISTRY_PATH,
) -> DevCalReport:
    """Run the development calibration on 2018 and write its files to ``out_dir``.

    Reads ``<data_dir>/v1/scada_2018.csv.gz`` and ``leaks_2018.csv`` (both checked against
    ``MANIFEST.json``; nothing of 2019) and the pressure sensors of the registry at
    ``registry_path`` (the official one by default). Writes ``residuals.csv.gz`` and
    ``folds.json`` (the out-of-fold residuals and their folds), ``calibration.dev-baseline-2018
    .json`` (sigma by ``calibrate``; ``h = artefact_h``, ``n_min = artefact_n``, ``h_close =
    h_close_ratio * artefact_h``), ``sweep.csv`` and ``report.md``.

    The sweep replays ``z = -residual / sigma`` for every ``h`` in ``h_values`` and ``N`` in
    ``n_values`` with ``k`` and ``c`` fixed and ``h_close = h_close_ratio * h``, so each ``h`` is
    swept on its own (``sweep`` takes one ``h_close``). ``artefact_h`` and ``artefact_n`` must be
    in the grids. ``match`` is the PROVISIONAL matching rule (Q-UI-06). No parameter has a
    default: they are all development choices (``NOT_PREREGISTERED``). The artefact is mock
    (``provenance = dev-baseline-2018``). Argument errors raise ``ValueError`` before anything is
    written; the outputs are byte-for-byte reproducible from the same inputs.
    """
    started = time.perf_counter()
    parameters = DevCalParameters(
        slot_minutes=slot_minutes,
        statistic=statistic,
        sigma_floor_m=sigma_floor_m,
        k=k,
        h_values=tuple(h_values),
        n_values=tuple(n_values),
        c=c,
        h_close_ratio=h_close_ratio,
        artefact_h=artefact_h,
        artefact_n=artefact_n,
        match=match,
    )
    _check_arguments(parameters)
    registry = SensorRegistry.load(registry_path)
    pressures = load_pressures_2018(data_dir, registry)
    leaks, leaks_sha256 = _load_leaks(data_dir)

    oof = oof_hour_of_week_residuals(pressures, slot_minutes=slot_minutes, statistic=statistic)
    residuals_sha256, folds_sha256 = write_oof(oof, out_dir)
    artefact = calibrate(
        residual_series(oof),
        provenance=PROVENANCE,
        forecaster=forecaster_identity(slot_minutes=slot_minutes, statistic=statistic),
        residuals_sha256=residuals_sha256,
        folds_sha256=folds_sha256,
        sigma_floor_m=sigma_floor_m,
        k=k,
        h=artefact_h,
        n_min=artefact_n,
        c=c,
        h_close=h_close_ratio * artefact_h,
    )
    artefact_path = out_dir / ARTEFACT_FILE
    write_artefact(artefact, artefact_path)

    residuals = oof.residuals
    index = pd.DatetimeIndex(residuals.index)
    sigma = np.array(list(artefact.sigma_m.values()), dtype=np.float64)
    z = np.ascontiguousarray(-residuals.to_numpy(dtype=np.float64) / sigma)
    z_table = pd.DataFrame(z, index=residuals.index, columns=residuals.columns)
    scales = diurnal_robust_scale(residuals)
    structure = pd.DataFrame(
        {
            "lag1_autocorr": lag1_autocorrelation(residuals),
            "diurnal_scale_ratio": diurnal_scale_ratio(scales, sigma_floor_m=sigma_floor_m),
            "common_mode_corr": common_mode_correlation(residuals),
            "extreme_rate": extreme_rate(z_table, threshold=EXTREME_Z_THRESHOLD),
        }
    ).rename_axis("sensor")
    masks = {policy: mascara_limpa(index, leaks, policy) for policy in MASK_POLICIES}

    swept = [
        sweep(
            z,
            index,
            leaks,
            k=k,
            h_values=[h],
            n_values=n_values,
            c=c,
            h_close=h_close_ratio * h,
            match=match,
        )
        for h in h_values
    ]
    table = pd.concat(swept, ignore_index=True)
    sweep_path = out_dir / SWEEP_FILE
    sweep_path.write_bytes(_sweep_csv(table).encode("utf-8"))

    report = DevCalReport(
        out_dir=out_dir,
        residuals_path=out_dir / "residuals.csv.gz",
        folds_path=out_dir / "folds.json",
        artefact_path=artefact_path,
        sweep_path=sweep_path,
        report_path=out_dir / REPORT_FILE,
        residuals_sha256=residuals_sha256,
        folds_sha256=folds_sha256,
        artefact_sha256=sha256(artefact_path),
        scada_sha256=_scada_sha256(data_dir),
        leaks_sha256=leaks_sha256,
        parameters=parameters,
        artefact=artefact,
        structure=structure,
        structure_ranges=range_summary(structure),
        pairwise=pairwise_correlation(residuals),
        diurnal_scale=scales,
        sigma_sensitivity={
            policy: sigma_sensitivity(residuals, mask, sigma_floor_m=sigma_floor_m)
            for policy, mask in masks.items()
        },
        clean_fraction={policy: float(mask.mean()) for policy, mask in masks.items()},
        sweep=table,
        elapsed_seconds=time.perf_counter() - started,
    )
    report.report_path.write_bytes(_report_text(report).encode("utf-8"))
    return report


def _sweep_csv(table: pd.DataFrame) -> str:
    """``sweep.csv``: the sweep table, the pipes of the missed leaks joined by ``;``."""
    out = table.copy()
    for column in out.columns:
        if str(column).startswith("missed_"):
            out[column] = out[column].map(";".join)
    return out.to_csv(index=False, lineterminator="\n")


# --- The report ----------------------------------------------------------------------------


def _cell(value: Any) -> str:
    if isinstance(value, float | np.floating):
        return "-" if math.isnan(value) else f"{float(value):.4g}"
    return str(value)


def _markdown_table(table: pd.DataFrame, *, index: bool = False) -> str:
    shown = table.reset_index() if index else table
    header = [str(c) for c in shown.columns]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in shown.itertuples(index=False):
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return "\n".join(lines)


def _hours_text(delta: timedelta) -> str:
    return f"{delta / timedelta(hours=1):.4g} h"


def _parameter_rows(p: DevCalParameters) -> pd.DataFrame:
    rows = [
        ("slot_minutes", p.slot_minutes, "baseline slot: day of week x minute of day // slot"),
        ("statistic", p.statistic, "baseline: reduction over the same slot of the other months"),
        ("sigma_floor_m", p.sigma_floor_m, "sigma = max(floor, 1.4826 MAD), metres"),
        ("k", p.k, f"allowance; implied delta = 2k = {2 * p.k:.4g} sigma"),
        ("h grid", ", ".join(_cell(h) for h in p.h_values), "swept values of h"),
        ("N grid", ", ".join(str(n) for n in p.n_values), "swept values of n_min"),
        ("c", p.c, "S is capped at c*h"),
        ("h_close_ratio", p.h_close_ratio, "h_close = h_close_ratio * h, for every h"),
        ("artefact h", p.artefact_h, "h written to the artefact"),
        ("artefact N", p.artefact_n, "n_min written to the artefact"),
        (
            "max_delay abrupt",
            _hours_text(p.match.max_delay["abrupt"]),
            "provisional matching rule (Q-UI-06)",
        ),
        (
            "max_delay incipient",
            _hours_text(p.match.max_delay["incipient"]),
            "provisional matching rule (Q-UI-06)",
        ),
    ]
    return pd.DataFrame(
        [(name, value, f"{NOT_PREREGISTERED}; {note}") for name, value, note in rows],
        columns=["parameter", "value", "note"],
    )


def _report_text(report: DevCalReport) -> str:
    p, a = report.parameters, report.artefact
    views = summarise_sweep(report.sweep)
    leaks_total = int(report.sweep["total"].iloc[0])
    artefact_rows = pd.DataFrame(
        [
            ("file", f"`{ARTEFACT_FILE}`"),
            ("provenance", f"`{a.provenance}` (is_mock = {str(a.is_mock).lower()})"),
            ("forecaster", f"`{a.forecaster.name}` `{a.forecaster.version}`"),
            ("h, N (n_min), h_close", f"{a.h:.4g}, {a.n_min}, {a.h_close:.4g}"),
            ("k, c", f"{a.k:.4g}, {a.c:.4g}"),
            ("residuals_sha256", f"`{report.residuals_sha256}`"),
            ("folds_sha256", f"`{report.folds_sha256}`"),
            ("artefact sha256", f"`{report.artefact_sha256}`"),
            ("data v1 scada_2018.csv.gz sha256", f"`{report.scada_sha256}`"),
            ("data v1 leaks_2018.csv sha256", f"`{report.leaks_sha256}`"),
        ],
        columns=["item", "value"],
    )
    pair = report.pairwise
    reference_rate = gaussian_extreme_rate(EXTREME_Z_THRESHOLD)
    sections = [
        "# Development calibration on 2018 (BE-012f)",
        f"> **Notice.** {NOTICE}",
        "Every number below comes from one run of `sentinel.detection.offline.devcal`. They "
        "describe a local hour-of-week baseline on a simulated network and are used to learn "
        "the structure of the residuals and to exercise the calibration and the sweep.",
        "## Parameters",
        _markdown_table(_parameter_rows(p)),
        "## Calibration artefact",
        "`residuals_sha256` is the sha256 of the **uncompressed** CSV text of "
        "`residuals.csv.gz` (it does not depend on the zlib build); `folds_sha256` and the "
        "artefact hash are those of the files.",
        _markdown_table(artefact_rows),
        "## Residual structure",
        "Residual = observed - expected (out of fold, metres); z = (expected - observed) / "
        "sigma = -residual / sigma, with the sigma of the artefact. Statistics per sensor:",
        "- `lag1_autocorr`: lag-1 autocorrelation of the residual.\n"
        "- `diurnal_scale_ratio`: largest over smallest hourly robust scale (1.4826 MAD per "
        "hour of the day, each floored at sigma_floor_m as sigma is).\n"
        "- `common_mode_corr`: correlation with the median across sensors of each window.\n"
        f"- `extreme_rate`: fraction of windows with |z| > {EXTREME_Z_THRESHOLD:g}; for "
        f"independent standard normal z it would be {reference_rate:.3g}.",
        "Range across the sensors:",
        _markdown_table(report.structure_ranges, index=True),
        f"Pairwise correlation between the residuals of different sensors ({pair.pairs} "
        f"pairs): median {pair.median:.4g}, maximum {pair.maximum:.4g} "
        f"({pair.maximum_pair[0]}, {pair.maximum_pair[1]}). Mean correlation with the common "
        f"mode: {report.structure['common_mode_corr'].mean():.4g}.",
        "Per sensor:",
        _markdown_table(report.structure, index=True),
        "## Sensitivity of sigma to the leak labels",
        f"sigma from all windows against sigma from the windows the P1 mask "
        f"(`sentinel.data.clean_mask.mascara_limpa`) leaves clean; ratio = sigma_all / "
        f"sigma_clean. The policy used is `{PRIMARY_MASK_POLICY}`: it excludes every window "
        "inside [start, end] of any labelled leak, i.e. the windows with an active leak. "
        "`reparados` (the P1 default, only the repaired leaks) is shown as a cross-check. "
        "When few windows remain the clean windows come from a short part of the year, so "
        "the ratio also reflects the period and not only the leaks. 2018 is not a clean "
        "normal year (ADR-008 Context).",
    ]
    for policy in MASK_POLICIES:
        table = report.sigma_sensitivity[policy]
        ratio = table["ratio"]
        sections += [
            f"### Policy `{policy}`" + (" (used)" if policy == PRIMARY_MASK_POLICY else ""),
            f"Clean windows: {report.clean_fraction[policy]:.1%} of the year. Ratio across "
            f"sensors: min {ratio.min():.4g} ({ratio.idxmin()}), median {ratio.median():.4g}, "
            f"max {ratio.max():.4g} ({ratio.idxmax()}).",
            _markdown_table(table, index=True),
        ]
    sections += [
        "## Sweep",
        f"One row per (h, N); k and c fixed, h_close = {p.h_close_ratio:.4g} h. Counts are "
        f"detected/total labelled leaks of 2018 ({leaks_total} leaks), with the provisional rule "
        "of the matching window [start, min(end, start + max_delay)]; no confidence "
        "intervals. A false alarm is an opening outside the window of every labelled leak; "
        "a repeat is an opening inside the windows of leaks that are all detected already. "
        "The false alarms are not monotone in h in general: read the table, do not "
        "interpolate it.",
        _markdown_table(views["counts"]),
        "Median delay of the detected leaks, in hours, from the label start and from the "
        "peak (negative: the alert opened before the peak); `-` if none was detected:",
        _markdown_table(views["delays_h"]),
        "Missed leaks (pipes):",
        _markdown_table(views["missed"]),
    ]
    return "\n\n".join(sections) + "\n"


# --- Command line --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sentinel.detection.offline.devcal",
        description=(
            "Development calibration on 2018 (dev-baseline-2018, is_mock). Every argument is "
            "required: all numbers are development choices, not pre-registered."
        ),
        allow_abbrev=False,
    )
    required = parser.add_argument_group("required arguments")

    def add(flag: str, **kwargs: Any) -> None:
        required.add_argument(flag, required=True, **kwargs)

    add("--data-dir", type=Path, help="directory that holds v1/ (SENTINEL_DATA_DIR)")
    add("--out-dir", type=Path, help="where the outputs are written (outside git)")
    add("--slot-minutes", type=int, help="baseline slot length; divides 1440")
    add("--statistic", choices=("median", "mean"), help="baseline reduction over the slot")
    add("--sigma-floor-m", type=float, help="floor of sigma, metres")
    add("--k", type=float, help="CUSUM allowance")
    add("--h", dest="h_values", nargs="+", type=float, help="values of h to sweep")
    add("--n", dest="n_values", nargs="+", type=int, help="values of N (n_min) to sweep")
    add("--c", type=float, help="cap factor: S is capped at c*h")
    add("--h-close-ratio", type=float, help="h_close = ratio * h, in (0, 1]")
    add("--artefact-h", type=float, help="h of the artefact (one of --h)")
    add("--artefact-n", type=int, help="N of the artefact (one of --n)")
    add("--max-delay-abrupt-h", type=float, help="provisional matching rule, abrupt leaks, hours")
    add("--max-delay-incipient-h", type=float, help="provisional rule, incipient leaks, hours")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    match = MatchRule(
        {
            "abrupt": timedelta(hours=args.max_delay_abrupt_h),
            "incipient": timedelta(hours=args.max_delay_incipient_h),
        }
    )
    report = run(
        args.data_dir,
        args.out_dir,
        slot_minutes=args.slot_minutes,
        statistic=args.statistic,
        sigma_floor_m=args.sigma_floor_m,
        k=args.k,
        h_values=args.h_values,
        n_values=args.n_values,
        c=args.c,
        h_close_ratio=args.h_close_ratio,
        artefact_h=args.artefact_h,
        artefact_n=args.artefact_n,
        match=match,
    )
    print("development calibration on 2018 (dev-baseline-2018, is_mock)")
    for label, path, digest in (
        ("residuals", report.residuals_path, f"content sha256 {report.residuals_sha256}"),
        ("folds", report.folds_path, f"sha256 {report.folds_sha256}"),
        ("artefact", report.artefact_path, f"sha256 {report.artefact_sha256}"),
        ("sweep", report.sweep_path, ""),
        ("report", report.report_path, ""),
    ):
        print(f"{label:<10}{path}  {digest}".rstrip())
    print(f"elapsed   {report.elapsed_seconds:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
