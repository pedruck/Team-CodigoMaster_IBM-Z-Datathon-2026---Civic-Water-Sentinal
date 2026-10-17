"""Shared helpers for the P2 (forecasting) notebooks.

Training code needs pandas/numpy, which are not backend dependencies, so it lives here and
not in ``src/sentinel``. Everything the *runtime* needs (feature definitions, forecasters)
is in ``src/sentinel/forecasting`` and is imported from there; the vectorised builders below
are checked against it row by row (``assert_feature_parity``) so training and inference can
never silently diverge.
"""

from __future__ import annotations

import glob
import importlib.metadata as md
import json
import os
import platform
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sentinel.forecasting.baseline import SLOTS
from sentinel.forecasting.features import (
    SensorLayout,
    gru_feature_names,
    gru_step_features,
    xgb_feature_names,
    xgb_features,
)
from sentinel.schemas.forecasts import ForecastRequest
from sentinel.schemas.windows import SensorWindow
from sentinel.sensors import SensorRegistry

TRAIN_END = "2018-10-31 23:55"  # Jan-Oct: training
VAL_START = "2018-11-01 00:00"  # Nov-Dec: validation (still 2018, never 2019)

# --- paths and platform -----------------------------------------------------------------------


def data_dir() -> Path:
    return Path(os.environ.get("SENTINEL_DATA_DIR", "~/shared/civicwater/data")).expanduser()


def models_dir() -> Path:
    path = Path(os.environ.get("SENTINEL_MODELS_DIR", "~/shared/civicwater/models")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def on_linuxone() -> bool:
    return platform.machine() == "s390x"


def _version(dist: str) -> str | None:
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        return None


def accelerator_report() -> dict[str, Any]:
    """Same checks as the organisers' notebooks: CPU feature, Snap ML, zDNN plugin, kernels."""
    nnpa = False
    try:
        nnpa = "nnpa" in Path("/proc/cpuinfo").read_text()
    except OSError:
        pass
    z_ops = glob.glob("/opt/venv/lib*/python3.*/site-packages/tensorflow/core/kernels/_z_ops.so")
    return {
        "machine": platform.machine(),
        "python": platform.python_version(),
        "nnpa_in_cpuinfo": nnpa,
        "snapml": _version("snapml"),
        "xgboost": _version("xgboost"),
        "tensorflow": _version("tensorflow") or _version("tensorflow-cpu"),
        "ibm-zdnn-plugin": _version("ibm-zdnn-plugin"),
        "tf_zdnn_kernels": z_ops[0] if z_ops else None,
        "onnx": _version("onnx"),
        "tf2onnx": _version("tf2onnx"),
    }


# --- data ---------------------------------------------------------------------------------------


def load_layout(version: str = "v1") -> SensorLayout:
    """Sensor order from the data owner's sensors.csv, read with the backend's own parser."""
    reg = SensorRegistry.load(data_dir() / version / "sensors.csv")
    return SensorLayout(pressure=reg.pressure, flow=reg.flow, tank_level=reg.tank_level)


def load_scada(year: int, version: str = "v1") -> pd.DataFrame:
    if year != 2018:
        raise ValueError("P2 trains and validates on 2018 only; 2019 is the test year")
    return pd.read_csv(
        data_dir() / version / f"scada_{year}.csv.gz", index_col="timestamp", parse_dates=True
    )


def load_leaks_2018(version: str = "v1") -> pd.DataFrame:
    leaks = pd.read_csv(data_dir() / version / "leaks_2018.csv", parse_dates=["start", "end"])
    return leaks


def leak_free_mask(index: pd.DatetimeIndex, leaks: pd.DataFrame) -> pd.Series:
    """True where no leak of the 2018 report is active (start <= t <= end)."""
    free = pd.Series(True, index=index)
    for _, leak in leaks.iterrows():
        free.loc[(index >= leak["start"]) & (index <= leak["end"])] = False
    return free


def split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    return df.loc[:TRAIN_END], df.loc[VAL_START:]


# --- vectorised features (must match sentinel.forecasting.features) -----------------------------


def xgb_frame(df: pd.DataFrame, layout: SensorLayout, target: str) -> pd.DataFrame:
    idx = df.index
    cols: dict[str, Any] = {}
    for s in layout.flow:
        cols[f"flow:{s}"] = df[s].to_numpy()
    for s in layout.tank_level:
        cols[f"level:{s}"] = df[s].to_numpy()
    cols["cal:hour"] = (idx.hour + idx.minute / 60.0).to_numpy(dtype=float)
    cols["cal:day_of_week"] = idx.dayofweek.to_numpy(dtype=float)
    for s in layout.pressure:
        if s != target:
            cols[f"pressure:{s}"] = df[s].to_numpy()
    frame = pd.DataFrame(cols, index=idx)
    assert list(frame.columns) == xgb_feature_names(layout, target)
    return frame


def gru_frame(df: pd.DataFrame, layout: SensorLayout) -> pd.DataFrame:
    idx = df.index
    hour = (idx.hour + idx.minute / 60.0).to_numpy(dtype=float)
    week = (idx.dayofweek.to_numpy(dtype=float) + hour / 24.0) / 7.0
    cols: dict[str, Any] = {}
    for s in layout.pressure:
        cols[f"pressure:{s}"] = df[s].to_numpy()
    for s in layout.flow:
        cols[f"flow:{s}"] = df[s].to_numpy()
    for s in layout.tank_level:
        cols[f"level:{s}"] = df[s].to_numpy()
    cols["cal:hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    cols["cal:hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    cols["cal:week_sin"] = np.sin(2 * np.pi * week)
    cols["cal:week_cos"] = np.cos(2 * np.pi * week)
    frame = pd.DataFrame(cols, index=idx)
    assert list(frame.columns) == gru_feature_names(layout)
    return frame


def row_to_window(ts: pd.Timestamp, row: pd.Series, layout: SensorLayout) -> SensorWindow:
    """Builds the contract object the API would receive for this row (naive time = UTC)."""
    stamp = ts.to_pydatetime().replace(tzinfo=UTC)
    return SensorWindow(
        schema_version="1.0",
        timestamp=stamp,
        pressures={s: float(row[s]) for s in layout.pressure},
        flows={s: float(row[s]) for s in layout.flow},
        tank_levels={s: float(row[s]) for s in layout.tank_level},
    )


def complete_rows(df: pd.DataFrame, layout: SensorLayout) -> pd.DataFrame:
    cols = [*layout.pressure, *layout.flow, *layout.tank_level]
    return df.dropna(subset=cols)


def assert_feature_parity(df: pd.DataFrame, layout: SensorLayout, n: int = 200) -> int:
    """Vectorised training features == runtime features, on ``n`` random complete rows."""
    rows = complete_rows(df, layout).sample(n=min(n, len(df)), random_state=7)
    gru = gru_frame(rows, layout).to_numpy()
    targets = list(layout.pressure)
    xgb = {t: xgb_frame(rows, layout, t).to_numpy() for t in targets}
    for i, (ts, row) in enumerate(rows.iterrows()):
        window = row_to_window(ts, row, layout)
        np.testing.assert_allclose(gru[i], gru_step_features(window, layout), rtol=0, atol=1e-9)
        for t in targets:
            np.testing.assert_allclose(xgb[t][i], xgb_features(window, layout, t), atol=1e-9)
    return len(rows)


def build_request(
    df: pd.DataFrame, ts: pd.Timestamp, layout: SensorLayout, history: int
) -> ForecastRequest:
    """ForecastRequest for ``ts`` with ``history`` past windows, exactly as the backend does."""
    past = df.loc[ts - timedelta(minutes=5 * history) : ts - timedelta(minutes=5)]
    if len(past) != history:
        raise ValueError(f"not enough contiguous history before {ts}")
    return ForecastRequest(
        target_timestamp=ts.to_pydatetime().replace(tzinfo=UTC),
        pressure_sensor_ids=tuple(layout.pressure),
        history=tuple(row_to_window(t, r, layout) for t, r in past.iterrows()),
        current=row_to_window(ts, df.loc[ts], layout),
    )


# --- baseline -------------------------------------------------------------------------------------


def hour_of_week_index(index: pd.DatetimeIndex) -> np.ndarray:
    return (index.dayofweek * 24 + index.hour).to_numpy()


def fit_hour_of_week(df: pd.DataFrame, layout: SensorLayout) -> pd.DataFrame:
    prof = df[list(layout.pressure)].groupby(hour_of_week_index(df.index)).median()
    prof = prof.reindex(range(SLOTS))
    prof = prof.fillna(df[list(layout.pressure)].median())
    return prof


def predict_hour_of_week(prof: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    values = prof.loc[hour_of_week_index(df.index)].to_numpy()
    return pd.DataFrame(values, index=df.index, columns=prof.columns)


# --- metrics --------------------------------------------------------------------------------------


def robust_sigma(resid: pd.DataFrame) -> pd.Series:
    """1.4826 * MAD: a standard deviation that ignores the few large (leak) residuals."""
    centred = resid - resid.median()
    return 1.4826 * centred.abs().median()


def mae_by_sensor(resid: pd.DataFrame) -> pd.Series:
    return resid.abs().mean()


def leak_contrast(resid: pd.DataFrame, sigma: pd.Series, free: pd.Series) -> float | None:
    """Mean |z| during known 2018 leaks divided by mean |z| without leaks (higher = leaks
    stand out more). ``None`` if the period has no leak windows."""
    z = (resid / sigma).abs().mean(axis=1)
    inside, outside = z[~free.reindex(z.index, fill_value=True)], z[free.reindex(z.index, fill_value=True)]
    if inside.empty or outside.empty:
        return None
    return float(inside.mean() / outside.mean())


# --- benchmark (same style as the organisers' notebooks) ----------------------------------------


def benchmark(fn: Callable[[], Any], repeats: int = 50, warmup: int = 3) -> dict[str, float]:
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    arr = np.asarray(times)
    return {
        "p50_ms": float(np.percentile(arr, 50)),
        "p99_ms": float(np.percentile(arr, 99)),
        "best_ms": float(arr.min()),
    }


# --- artefacts ----------------------------------------------------------------------------------


def artefact_meta(name: str, version: str, layout: SensorLayout, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "version": version,
        "data_version": "v1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "trained_on_machine": platform.machine(),
        "layout": layout.to_dict(),
        **extra,
    }


def write_json(path: Path, obj: Any) -> Path:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
