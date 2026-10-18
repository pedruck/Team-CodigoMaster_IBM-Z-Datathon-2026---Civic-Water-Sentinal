"""Out-of-fold 2018 residuals of the XGBoost forecaster for the detector calibration (DEP-DET-05).

The folds are the 12 calendar months of 2018. For each month, one XGBoost per pressure sensor
is trained on the clean windows (P1's mask) of the **other** 11 months and predicts every
window of the held-out month, leak periods included. Each labelled leak is therefore
predicted by models that never saw its month. The number of trees per sensor is the one of
the delivered model (its ``best_iteration``), so no fold early-stops on the month it predicts.

Two variants answer Q-ML-07:

- ``with-neighbours``: the delivered ``xgboost-33`` features (flows, tank level, calendar and
  the other 32 pressure sensors at the same instant);
- ``without-neighbours``: flows, tank level and calendar only. Not a delivered model: its
  trees per sensor come from the same early-stopping protocol as the delivered one
  (fit Jan-Oct, stop on Nov-Dec, notebook 02).

Output per variant, outside git (``$SENTINEL_DATA_DIR/v1/oof/<model>/<variant>/``):
``residuals_long.csv.gz`` (``timestamp, sensor, observed, expected, fold``), ``folds.json``
and ``declaration.json`` (which readings the expected value depends on, Q-ML-07/08, plus the
hashes and a summary). Offline tool (pandas, numpy, xgboost); the server never imports it.
Reads 2018 only.

    python -m sentinel.forecasting.oof --model-dir ~/shared/civicwater/models/xgboost-33@0.1.0
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sentinel.forecasting import training
from sentinel.forecasting.features import SensorLayout, xgb_feature_names

VARIANTS = ("with-neighbours", "without-neighbours")
LONG_FILE = "residuals_long.csv.gz"
FOLDS_FILE = "folds.json"
DECLARATION_FILE = "declaration.json"
CACHE_DIR = ".folds"
FLOAT_FORMAT = "%.6f"  # 1 µm: three orders below the smallest sigma (3 mm)
CSV_CHUNK_ROWS = 16_384
SUMMARY_POLICIES = ("reparados", "reparados+crescimento")


def _xgboost() -> Any:
    return importlib.import_module("xgboost")  # offline only, absent from backend CI


def variant_features(layout: SensorLayout, target: str, variant: str) -> list[str]:
    """Feature names of ``target`` in ``variant``, in the delivered model's order."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}, got {variant!r}")
    names = xgb_feature_names(layout, target)
    if variant == "without-neighbours":
        names = [n for n in names if not n.startswith("pressure:")]
    return names


def month_folds(index: pd.DatetimeIndex) -> pd.Series[int]:
    """Fold of each window = its calendar month (1..12). One year only."""
    years = sorted(set(index.year))
    if len(years) != 1:
        raise ValueError(f"the windows must lie within one calendar year, got {years}")
    return pd.Series(index.month.to_numpy(), index=index, name="fold")


def declaration(layout: SensorLayout, variant: str) -> dict[str, Any]:
    """Q-ML-07/08: which observed readings the expected value of a sensor depends on."""
    other = variant == "with-neighbours"
    return {
        "target_own_pressure": "never (no lag, no current value)",
        "other_pressures_same_window": other,
        "flows_same_window": list(layout.flow),
        "tank_level_same_window": list(layout.tank_level),
        "calendar": ["hour of day (fraction)", "day of week"],
        "history": "none (min_history_windows = 1: only the window being scored)",
        "note": (
            "the other 32 pressure sensors at the same instant are inputs, so a leak that "
            "lowers neighbouring sensors can partly move the expected value with it"
            if other
            else "no pressure reading is an input: a pressure drop cannot move the expected value"
        ),
    }


def fold_expected(
    df: pd.DataFrame,
    layout: SensorLayout,
    clean: pd.Series[bool],
    folds: pd.Series[int],
    fold: int,
    variant: str,
    params: dict[str, Any],
    n_trees: dict[str, int],
    sensors: Sequence[str],
) -> pd.DataFrame:
    """Expected pressures of the windows of ``fold``, from models trained on the clean windows
    of the other folds. One column per sensor of ``sensors``."""
    xgb = _xgboost()
    held = (folds == fold).to_numpy()
    train_rows = (~held) & clean.reindex(df.index, fill_value=False).to_numpy()
    if not train_rows.any():
        raise ValueError(f"fold {fold:02d}: no clean training window outside the fold")
    fit_params = {k: v for k, v in params.items() if k != "early_stopping_rounds"}
    predict_on = df[held]
    out: dict[str, Any] = {}
    for sensor in sensors:
        names = variant_features(layout, sensor, variant)
        tr = df[train_rows]
        x_tr = training.xgb_frame(tr, layout, sensor)[names]
        y_tr = tr[sensor]
        ok = y_tr.notna().to_numpy()
        model = xgb.XGBRegressor(**fit_params | {"n_estimators": int(n_trees[sensor])})
        model.fit(x_tr[ok], y_tr[ok], verbose=False)
        x_ho = training.xgb_frame(predict_on, layout, sensor)[names]
        out[sensor] = model.predict(x_ho).astype(np.float64)
    return pd.DataFrame(out, index=predict_on.index)


def early_stopped_trees(
    df: pd.DataFrame,
    layout: SensorLayout,
    clean: pd.Series[bool],
    variant: str,
    params: dict[str, Any],
    sensors: Sequence[str],
) -> dict[str, int]:
    """Trees per sensor with notebook 02's protocol: fit on clean Jan-Oct, early-stop on
    Nov-Dec. Used for the variant that has no delivered model."""
    xgb = _xgboost()
    train, val = training.split(df)
    tr = train[clean.reindex(train.index, fill_value=False).to_numpy()]
    trees: dict[str, int] = {}
    for sensor in sensors:
        names = variant_features(layout, sensor, variant)
        x_tr, y_tr = training.xgb_frame(tr, layout, sensor)[names], tr[sensor]
        x_va, y_va = training.xgb_frame(val, layout, sensor)[names], val[sensor]
        ok_tr, ok_va = y_tr.notna().to_numpy(), y_va.notna().to_numpy()
        model = xgb.XGBRegressor(**params)
        model.fit(x_tr[ok_tr], y_tr[ok_tr], eval_set=[(x_va[ok_va], y_va[ok_va])], verbose=False)
        trees[sensor] = int(model.best_iteration) + 1
    return trees


def oof_expected(
    df: pd.DataFrame,
    layout: SensorLayout,
    clean: pd.Series[bool],
    variant: str,
    params: dict[str, Any],
    n_trees: dict[str, int],
    *,
    sensors: Sequence[str] | None = None,
    cache_dir: Path | None = None,
    progress: Callable[[str], None] = print,
) -> tuple[pd.DataFrame, pd.Series[int]]:
    """Out-of-fold expected pressures of every window (index of ``df``) and their fold.

    With ``cache_dir``, each finished fold is stored there and reused by a later run with the
    same arguments, so an interrupted run resumes instead of starting over.
    """
    sensors = list(sensors or layout.pressure)
    folds = month_folds(pd.DatetimeIndex(df.index))
    parts = []
    for fold in sorted(set(folds.tolist())):
        cached = cache_dir / f"fold-{fold:02d}.csv.gz" if cache_dir else None
        if cached is not None and cached.exists():
            part = pd.read_csv(cached, index_col=0, parse_dates=True, float_precision="round_trip")
            if list(part.columns) == sensors:
                parts.append(part)
                progress(f"{variant} fold {fold:02d}: reused {cached.name}")
                continue
        t0 = time.perf_counter()
        part = fold_expected(df, layout, clean, folds, fold, variant, params, n_trees, sensors)
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            part.to_csv(cached, float_format="%.17g")
        parts.append(part)
        progress(f"{variant} fold {fold:02d}: {time.perf_counter() - t0:.0f} s")
    expected = pd.concat(parts).reindex(df.index)
    return expected, folds


def long_table(
    observed: pd.DataFrame, expected: pd.DataFrame, folds: pd.Series[int]
) -> pd.DataFrame:
    """``timestamp, sensor, observed, expected, fold``, ordered by timestamp then sensor
    (registry order). Windows with a missing observation are left out."""
    sensors = list(expected.columns)
    obs = observed[sensors].to_numpy(dtype=np.float64)
    exp = expected.to_numpy(dtype=np.float64)
    n_rows, n_sensors = obs.shape
    table = pd.DataFrame(
        {
            "timestamp": np.repeat(observed.index.to_numpy(), n_sensors),
            "sensor": np.tile(np.asarray(sensors, dtype=object), n_rows),
            "observed": obs.ravel(),
            "expected": exp.ravel(),
            "fold": np.repeat(folds.reindex(observed.index).to_numpy(), n_sensors),
        }
    )
    return table[table["observed"].notna() & table["expected"].notna()].reset_index(drop=True)


def write_long(table: pd.DataFrame, path: Path) -> str:
    """Write the long table as gzip CSV; return the sha256 of the **uncompressed** text (the
    detector owner's convention: the ``.gz`` bytes depend on the zlib build)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
    ):
        for start in range(0, len(table), CSV_CHUNK_ROWS):
            text = table.iloc[start : start + CSV_CHUNK_ROWS].to_csv(
                header=start == 0,
                index=False,
                float_format=FLOAT_FORMAT,
                date_format="%Y-%m-%d %H:%M:%S",
                lineterminator="\n",
            )
            data = text.encode("utf-8")
            digest.update(data)
            compressed.write(data)
    return digest.hexdigest()


def folds_document(folds: pd.Series[int]) -> dict[str, Any]:
    """Month -> [first, last window stamp], as in the detector owner's ``folds.json``."""
    index = pd.DatetimeIndex(folds.index)
    values = folds.to_numpy()
    return {
        "folds": {
            f"{m:02d}": [index[values == m].min().isoformat(), index[values == m].max().isoformat()]
            for m in sorted(set(values.tolist()))
        },
        "rule": "calendar month of the window (end of the 5-minute interval)",
    }


def summary(
    observed: pd.DataFrame, expected: pd.DataFrame, masks: dict[str, pd.Series[bool]]
) -> dict[str, Any]:
    """MAE, sigma (1.4826·MAD) and leak contrast of the out-of-fold residuals per mask policy.
    Informative only: the detector owner computes the calibration."""
    resid = observed[expected.columns] - expected
    out: dict[str, Any] = {}
    for policy, clean in masks.items():
        free = clean.reindex(resid.index, fill_value=False)
        sigma = training.robust_sigma(resid[free.to_numpy()])
        out[policy] = {
            "clean_windows": int(free.sum()),
            "mae_m_mean": round(float(training.mae_by_sensor(resid, free).mean()), 5),
            "sigma_m": sigma.round(5).to_dict(),
            "leak_contrast": training.leak_contrast(resid, sigma, free),
        }
    return out


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def run(
    model_dir: Path,
    out_root: Path,
    *,
    variants: Sequence[str] = VARIANTS,
    sensors: Sequence[str] | None = None,
    fast: bool = False,
    progress: Callable[[str], None] = print,
) -> dict[str, Path]:
    """Generate the out-of-fold residuals of each variant; return the folder of each."""
    meta = training.read_json(model_dir / "meta.json")
    layout = SensorLayout.from_dict(meta["layout"])
    policy = str(meta.get("mask_policy", training.DEFAULT_MASK_POLICY))
    params = dict(meta["params"])
    official_trees = {s: int(n) for s, n in meta["best_iteration"].items()}
    model_id = f"{meta['name']}@{meta['version']}"
    if fast:  # smoke test: same code path, few trees, separate folder
        out_root = out_root / "fast"
        params["n_estimators"] = 20
        official_trees = {s: min(n, 20) for s, n in official_trees.items()}

    df = training.load_scada(2018, layout)
    if list(layout.pressure) != list(meta["sensors"]):
        raise ValueError("the model's sensors differ from its layout")
    clean = training.clean_mask(pd.DatetimeIndex(df.index), policy)
    masks = {p: training.clean_mask(pd.DatetimeIndex(df.index), p) for p in SUMMARY_POLICIES}
    chosen = list(sensors or layout.pressure)
    progress(
        f"{model_id}: {len(df)} windows, {len(chosen)} sensors, training mask {policy} "
        f"({clean.mean():.1%} clean), variants {list(variants)}"
    )

    written: dict[str, Path] = {}
    for variant in variants:
        out = out_root / model_id / variant
        if variant == "with-neighbours":
            trees, trees_from = official_trees, f"best_iteration of {model_id}"
        else:
            progress(f"{variant}: early stopping on Nov-Dec to fix the trees per sensor")
            trees = early_stopped_trees(df, layout, clean, variant, params, chosen)
            trees_from = "early stopping, fit Jan-Oct / stop Nov-Dec 2018 (notebook 02 protocol)"
        expected, folds = oof_expected(
            df,
            layout,
            clean,
            variant,
            params,
            trees,
            sensors=chosen,
            cache_dir=out / CACHE_DIR,
            progress=progress,
        )
        table = long_table(df, expected, folds)
        residuals_sha = write_long(table, out / LONG_FILE)
        folds_text = _canonical(folds_document(folds))
        (out / FOLDS_FILE).write_text(folds_text, encoding="utf-8")
        forecaster = (
            {"name": meta["name"], "version": meta["version"]}
            if variant == "with-neighbours"
            else {"name": "xgboost-33-without-neighbours", "version": "candidate"}
        )
        document = {
            "dep": "DEP-DET-05",
            "forecaster": forecaster,
            "variant": variant,
            "delivered": variant == "with-neighbours",
            "data_version": "v1",
            "year": 2018,
            "inputs": declaration(layout, variant),
            "training": {
                "mask_policy": policy,
                "folds": "12 calendar months; each fold trains on the clean windows of the "
                "other 11 and predicts every window of its month (leaks included)",
                "trees_per_sensor": trees,
                "trees_from": trees_from,
                "params": {k: v for k, v in params.items() if k != "early_stopping_rounds"},
                "fast_mode": fast,
            },
            "files": {
                LONG_FILE: {
                    "columns": ["timestamp", "sensor", "observed", "expected", "fold"],
                    "rows": len(table),
                    "sha256_of_csv_text": residuals_sha,
                    "float_format": FLOAT_FORMAT,
                    "timestamp": "naive, end of the 5-minute window, as in scada_2018.csv.gz",
                    "residual": "observed - expected (z = (expected - observed)/sigma)",
                },
                FOLDS_FILE: {"sha256": _sha256_text(folds_text)},
            },
            "summary_by_mask": summary(df, expected, masks),
            "machine": training.accelerator_report().get("machine"),
        }
        (out / DECLARATION_FILE).write_text(_canonical(document), encoding="utf-8")
        progress(f"{variant}: {len(table)} rows -> {out}")
        written[variant] = out
    return written


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model-dir", type=Path, required=True, help="delivered xgboost-33@<v>/")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output root (default $SENTINEL_DATA_DIR/v1/oof)",
    )
    ap.add_argument("--variant", choices=VARIANTS, action="append", help="default: both")
    ap.add_argument("--sensor", action="append", help="only these sensors (debugging)")
    ap.add_argument("--fast", action="store_true", help="20 trees: smoke test, not a result")
    args = ap.parse_args(argv)
    out_root = args.out or training.data_dir() / "v1" / "oof"
    run(
        args.model_dir.expanduser(),
        out_root.expanduser(),
        variants=args.variant or VARIANTS,
        sensors=args.sensor,
        fast=args.fast,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
