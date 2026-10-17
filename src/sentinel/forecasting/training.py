"""Training helpers for the forecasting notebooks (ML owner).

Vectorised (pandas) versions of the features in ``sentinel.forecasting.features``, the data
access used for training, and the metrics and benchmarks reported in the notebooks. Inference
never imports this module: the forecasters only need ``features`` and their artefacts.

Data rules (``docs/data.md``): P2 trains and validates on 2018 only; the training mask is the
data owner's ``sentinel.data.clean_mask`` (default policy ``reparados``); 2019 is never read.
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

from sentinel.data.clean_mask import carregar_vazamentos, mascara_limpa
from sentinel.forecasting.baseline import SLOTS
from sentinel.forecasting.features import (
    SensorLayout,
    canonical_id,
    gru_feature_names,
    gru_step_features,
    xgb_feature_names,
    xgb_features,
)
from sentinel.schemas.forecasts import ForecastRequest
from sentinel.schemas.windows import SensorWindow
from sentinel.sensors import SensorRegistry

TRAIN_END = pd.Timestamp("2018-10-31 23:55")  # Jan-Oct 2018: training
VAL_START = pd.Timestamp("2018-11-01 00:00")  # Nov-Dec 2018: validation (never 2019)
DEFAULT_REGISTRY = Path("config/sensors.battledim.json")
COLUMN_PREFIX = {"pressure": "Pressure_", "flow": "Flow_", "tank_level": "Level_"}
DEFAULT_MASK_POLICY = "reparados"

# --- paths and platform -----------------------------------------------------------------------


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


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
    try:
        nnpa = "nnpa" in Path("/proc/cpuinfo").read_text()
    except OSError:
        nnpa = False
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
        "torch": _version("torch"),
        "onnx": _version("onnx"),
    }


# --- data ---------------------------------------------------------------------------------------


def load_layout(registry: str | Path | None = None) -> SensorLayout:
    """Sensor IDs and order from the same registry the API uses (``SENTINEL_SENSOR_REGISTRY``,
    default the official ``config/sensors.battledim.json``)."""
    path = Path(registry or os.environ.get("SENTINEL_SENSOR_REGISTRY") or DEFAULT_REGISTRY)
    if not path.is_absolute() and not path.exists():
        path = repo_root() / path
    reg = SensorRegistry.load(path)
    if reg.provisional:
        raise ValueError(f"{path} is provisional (placeholder IDs); train with the official one")
    return SensorLayout(pressure=reg.pressure, flow=reg.flow, tank_level=reg.tank_level)


def column_of(sensor_id: str, kind: str) -> str:
    """Data owner's CSV column of a sensor ID in either spelling (``n1`` -> ``Pressure_n1``)."""
    prefix = COLUMN_PREFIX[kind]
    return sensor_id if sensor_id.startswith(prefix) else prefix + canonical_id(sensor_id)


def by_ids(raw: pd.DataFrame, layout: SensorLayout) -> pd.DataFrame:
    """The 37 sensor columns renamed to the layout IDs, in layout order."""
    mapping: dict[str, str] = {}
    for kind, ids in (
        ("pressure", layout.pressure),
        ("flow", layout.flow),
        ("tank_level", layout.tank_level),
    ):
        for sensor in ids:
            mapping[column_of(sensor, kind)] = sensor
    missing = [c for c in mapping if c not in raw.columns]
    if missing:
        raise KeyError(f"columns missing from the SCADA file: {missing}")
    return raw[list(mapping)].rename(columns=mapping)


def load_scada(year: int, layout: SensorLayout, version: str = "v1") -> pd.DataFrame:
    if year != 2018:
        raise ValueError("P2 trains and validates on 2018 only; 2019 is the test year")
    raw = pd.read_csv(
        data_dir() / version / f"scada_{year}.csv.gz", index_col="timestamp", parse_dates=True
    )
    return by_ids(raw, layout)


def load_leaks_2018(version: str = "v1") -> pd.DataFrame:
    return carregar_vazamentos(str(data_dir()), version)


def clean_mask(
    index: pd.DatetimeIndex,
    policy: str = DEFAULT_MASK_POLICY,
    margin_windows: int = 0,
    version: str = "v1",
) -> pd.Series[bool]:
    """The data owner's training mask (``sentinel.data.clean_mask``). True = clean window."""
    return mascara_limpa(index, load_leaks_2018(version), policy, margin_windows)


def split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    return df.loc[:TRAIN_END], df.loc[VAL_START:]


def sequence_weights(clean: pd.Series[bool], lag: int) -> np.ndarray[Any, Any]:
    """1.0 for targets whose ``lag`` input windows *and* the target are all clean, else 0.0.

    Sequence models must only learn from contiguous clean stretches (``docs/data.md`` §7).
    """
    rolled = clean.astype("float32").rolling(lag + 1, min_periods=lag + 1).min()
    return rolled.fillna(0.0).to_numpy(dtype=np.float32)


# --- vectorised features (must match sentinel.forecasting.features) -----------------------------


def _hours(index: pd.DatetimeIndex) -> np.ndarray[Any, Any]:
    return (index.hour + index.minute / 60.0).to_numpy(dtype=float)


def xgb_frame(df: pd.DataFrame, layout: SensorLayout, target: str) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    cols: dict[str, Any] = {}
    for s in layout.flow:
        cols[f"flow:{s}"] = df[s].to_numpy()
    for s in layout.tank_level:
        cols[f"level:{s}"] = df[s].to_numpy()
    cols["cal:hour"] = _hours(idx)
    cols["cal:day_of_week"] = idx.dayofweek.to_numpy(dtype=float)
    for s in layout.pressure:
        if s != target:
            cols[f"pressure:{s}"] = df[s].to_numpy()
    frame = pd.DataFrame(cols, index=idx)
    if list(frame.columns) != xgb_feature_names(layout, target):
        raise AssertionError("xgb_frame columns differ from xgb_feature_names")
    return frame


def gru_frame(df: pd.DataFrame, layout: SensorLayout) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    hour = _hours(idx)
    week = (idx.dayofweek.to_numpy(dtype=float) + hour / 24.0) / 7.0
    cols: dict[str, Any] = {}
    for s in (*layout.pressure, *layout.flow, *layout.tank_level):
        kind = "pressure" if s in layout.pressure else "flow" if s in layout.flow else "level"
        cols[f"{kind}:{s}"] = df[s].to_numpy()
    cols["cal:hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    cols["cal:hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    cols["cal:week_sin"] = np.sin(2 * np.pi * week)
    cols["cal:week_cos"] = np.cos(2 * np.pi * week)
    frame = pd.DataFrame(cols, index=idx)
    if list(frame.columns) != gru_feature_names(layout):
        raise AssertionError("gru_frame columns differ from gru_feature_names")
    return frame


def row_to_window(ts: pd.Timestamp, row: Any, layout: SensorLayout) -> SensorWindow:
    """The contract object the API receives for this row (timezone-naive time = UTC)."""
    stamp = ts.to_pydatetime().replace(tzinfo=UTC)
    return SensorWindow(
        schema_version="1.0",
        timestamp=stamp,
        pressures={s: float(row[s]) for s in layout.pressure},
        flows={s: float(row[s]) for s in layout.flow},
        tank_levels={s: float(row[s]) for s in layout.tank_level},
    )


def complete_rows(df: pd.DataFrame, layout: SensorLayout) -> pd.DataFrame:
    return df.dropna(subset=[*layout.pressure, *layout.flow, *layout.tank_level])


def assert_feature_parity(df: pd.DataFrame, layout: SensorLayout, n: int = 200) -> int:
    """Vectorised training features == runtime features, on ``n`` random complete rows."""
    rows = complete_rows(df, layout)
    rows = rows.sample(n=min(n, len(rows)), random_state=7)
    gru = gru_frame(rows, layout).to_numpy()
    xgb = {t: xgb_frame(rows, layout, t).to_numpy() for t in layout.pressure}
    for i, ts in enumerate(pd.DatetimeIndex(rows.index)):
        window = row_to_window(ts, rows.iloc[i], layout)
        np.testing.assert_allclose(gru[i], gru_step_features(window, layout), rtol=0, atol=1e-9)
        for target in layout.pressure:
            runtime = xgb_features(window, layout, target)
            np.testing.assert_allclose(xgb[target][i], runtime, rtol=0, atol=1e-9)
    return len(rows)


def build_request(
    df: pd.DataFrame, ts: pd.Timestamp, layout: SensorLayout, history: int
) -> ForecastRequest:
    """ForecastRequest for ``ts`` with ``history`` past windows, as the backend builds it."""
    past = df.loc[ts - timedelta(minutes=5 * history) : ts - timedelta(minutes=5)]
    if len(past) != history:
        raise ValueError(f"not enough contiguous history before {ts}")
    return ForecastRequest(
        target_timestamp=ts.to_pydatetime().replace(tzinfo=UTC),
        pressure_sensor_ids=tuple(layout.pressure),
        history=tuple(
            row_to_window(t, past.iloc[i], layout)
            for i, t in enumerate(pd.DatetimeIndex(past.index))
        ),
        current=row_to_window(ts, df.loc[ts], layout),
    )


# --- baseline -------------------------------------------------------------------------------------


def hour_of_week_index(index: pd.DatetimeIndex) -> np.ndarray[Any, Any]:
    return (index.dayofweek * 24 + index.hour).to_numpy()


def fit_hour_of_week(df: pd.DataFrame, layout: SensorLayout) -> pd.DataFrame:
    pressures = df[list(layout.pressure)]
    prof = pressures.groupby(hour_of_week_index(pd.DatetimeIndex(df.index))).median()
    prof = prof.reindex(range(SLOTS))
    return prof.fillna(pressures.median())


def predict_hour_of_week(prof: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    values = prof.loc[hour_of_week_index(pd.DatetimeIndex(df.index))].to_numpy()
    return pd.DataFrame(values, index=df.index, columns=prof.columns)


# --- metrics --------------------------------------------------------------------------------------


def robust_sigma(resid: pd.DataFrame) -> pd.Series[float]:
    """1.4826 * MAD: a standard deviation that ignores the few large (leak) residuals."""
    centred = resid - resid.median()
    return 1.4826 * centred.abs().median()


def mae_by_sensor(resid: pd.DataFrame) -> pd.Series[float]:
    return resid.abs().mean()


def leak_contrast(
    resid: pd.DataFrame, sigma: pd.Series[float], clean: pd.Series[bool]
) -> float | None:
    """Mean |z| in windows the training mask marks as leak-affected, divided by mean |z| in
    clean windows (higher = leaks stand out more). ``None`` if either set is empty."""
    z = (resid / sigma).abs().mean(axis=1)
    affected = ~clean.reindex(z.index, fill_value=True)
    inside, outside = z[affected], z[~affected]
    if inside.empty or outside.empty:
        return None
    return float(inside.mean() / outside.mean())


def low_variance_sensors(sigma: pd.Series[float], below_m: float = 0.02) -> dict[str, float]:
    """Sensors whose sigma is so small that z inflates (``docs/data.md`` §11: n215, n740)."""
    small = sigma[sigma < below_m].sort_values()
    return {str(k): round(float(v), 5) for k, v in small.items()}


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
