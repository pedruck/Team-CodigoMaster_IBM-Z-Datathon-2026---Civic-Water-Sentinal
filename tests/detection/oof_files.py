"""Synthetic files in the format of the ML owner's out-of-fold residuals (DEP-DET-05).

Everything is invented for the tests: the sensors, the pressures, the leaks and their size, the
forecaster identity. Nothing comes from data and no number is a result. The layout follows
``sentinel.forecasting.oof`` (long CSV ``timestamp, sensor, observed, expected, fold`` written
with ``%.6f``, ``folds.json``, ``declaration.json`` with the hash of the uncompressed CSV text).
"""

import gzip
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from sentinel.sensors import SensorRegistry

SENSORS = ("P1", "P2", "P3", "P4")
FORECASTER = {"name": "test-forecaster", "version": "0.1.0"}


def registry(sensors: Sequence[str] = SENSORS) -> SensorRegistry:
    return SensorRegistry(
        pressure=tuple(sensors), flow=(), tank_level=(), provisional=True, source="tests"
    )


@dataclass(frozen=True)
class Drop:
    """An invented pressure drop (metres) on some sensors from ``start`` until ``end``."""

    start: str
    end: str
    metres: Mapping[str, float]


def wide_frames(
    *,
    start: str,
    windows: int,
    seed: int,
    noise_m: float,
    drops: Sequence[Drop] = (),
    sensors: Sequence[str] = SENSORS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Observed (rounded to the centimetre, like the SCADA file) and expected pressures."""
    index = pd.date_range(start, periods=windows, freq="5min")
    rng = np.random.default_rng(seed)
    day = np.sin(2 * np.pi * np.arange(windows) / 288)
    expected = pd.DataFrame(
        {s: 40.0 + 2 * i + 0.3 * day for i, s in enumerate(sensors)}, index=index
    )
    observed = expected + rng.normal(0.0, noise_m, (windows, len(sensors)))
    for drop in drops:
        inside = (index > pd.Timestamp(drop.start)) & (index <= pd.Timestamp(drop.end))
        for sensor, metres in drop.metres.items():
            observed.loc[inside, sensor] -= metres
    return observed.round(2), expected


def long_table(observed: pd.DataFrame, expected: pd.DataFrame) -> pd.DataFrame:
    sensors = list(observed.columns)
    n = len(sensors)
    index = pd.DatetimeIndex(observed.index)
    return pd.DataFrame(
        {
            "timestamp": np.repeat(index.to_numpy(), n),
            "sensor": np.tile(np.asarray(sensors, dtype=object), len(index)),
            "observed": observed.to_numpy().ravel(),
            "expected": expected.to_numpy().ravel(),
            "fold": np.repeat(index.month.to_numpy(), n),
        }
    )


def folds_text(index: pd.DatetimeIndex) -> str:
    folds = {
        f"{m:02d}": [
            index[index.month == m].min().isoformat(),
            index[index.month == m].max().isoformat(),
        ]
        for m in sorted(set(index.month))
    }
    return json.dumps({"folds": folds, "rule": "calendar month"}, indent=2, sort_keys=True) + "\n"


def write_variant(
    directory: Path,
    table: pd.DataFrame,
    *,
    folds: str,
    declaration: Callable[[dict[str, object]], dict[str, object]] = lambda d: d,
) -> tuple[str, str]:
    """Write the three files; return ``(residuals_sha256, folds_sha256)``. ``declaration`` may
    edit the declaration before it is written (to test the refusals)."""
    directory.mkdir(parents=True, exist_ok=True)
    text = table.to_csv(
        index=False, float_format="%.6f", date_format="%Y-%m-%d %H:%M:%S", lineterminator="\n"
    ).encode()
    (directory / "residuals_long.csv.gz").write_bytes(gzip.compress(text, mtime=0))
    (directory / "folds.json").write_text(folds, encoding="utf-8")
    residuals_sha = hashlib.sha256(text).hexdigest()
    folds_sha = hashlib.sha256(folds.encode()).hexdigest()
    document: dict[str, object] = {
        "forecaster": dict(FORECASTER),
        "year": 2018,
        "files": {
            "residuals_long.csv.gz": {"sha256_of_csv_text": residuals_sha},
            "folds.json": {"sha256": folds_sha},
        },
    }
    (directory / "declaration.json").write_text(json.dumps(declaration(document)), encoding="utf-8")
    return residuals_sha, folds_sha


LEAK_COLUMNS = ("pipe", "start", "end", "leak_size", "type", "peak", "repaired_in_2018")


def write_data_dir(root: Path, leaks: Sequence[tuple[str, str, str, str, str, bool]]) -> str:
    """``<root>/v1/leaks_2018.csv`` from ``(pipe, start, end, type, peak, repaired)`` rows and a
    ``MANIFEST.json`` with its hash; return that hash."""
    v1 = root / "v1"
    v1.mkdir(parents=True, exist_ok=True)
    lines = [",".join(LEAK_COLUMNS)]
    lines += [f"{p},{s},{e},0.01,{t},{k},{r}" for p, s, e, t, k, r in leaks]
    data = ("\n".join(lines) + "\n").encode()
    (v1 / "leaks_2018.csv").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    manifest = {"files": {"leaks_2018.csv": {"sha256": digest}}}
    (v1 / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    return digest
