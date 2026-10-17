"""Tests of the offline hour-of-week baseline (BE-012f, ADR-008 §A and §E).

Synthetic cases use invented numbers: a made-up weekly pressure profile plus seeded noise.
The real-data test is skipped without ``SENTINEL_DATA_DIR`` (same pattern as
``tests/test_p1_data.py``) and checks shape and finiteness only, never result values.
"""

import calendar
import dataclasses
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sentinel.detection.calibration import ForecasterIdentity, calibrate
from sentinel.detection.offline.baseline import (
    OofResiduals,
    Statistic,
    load_pressures_2018,
    oof_hour_of_week_residuals,
    residual_series,
    write_oof,
)
from sentinel.sensors import SensorRegistry

SENSORS = ("a", "b", "c")  # invented sensor ids
NOISE_SD_M = 0.05  # invented noise level, metres


def synthetic_year(seed: int = 20180101) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly stamps of 2018 (end of window): ``(observed, profile)``.

    ``profile`` depends only on (day of week, hour); ``observed`` is the profile plus normal
    noise from a seeded generator. All numbers are invented.
    """
    idx = pd.date_range("2018-01-01 00:00", "2018-12-31 23:00", freq="1h", name="timestamp")
    how = np.asarray(idx.dayofweek * 24 + idx.hour, dtype=float)  # hour of week, 0..167
    profile = pd.DataFrame(
        {
            "a": 40.0 + 0.1 * how,
            "b": 55.0 + 2.0 * np.sin(2.0 * np.pi * how / 24.0),
            "c": 30.0 + 4.0 * np.cos(2.0 * np.pi * how / 168.0),
        },
        index=idx,
    )
    noise = np.random.default_rng(seed).normal(0.0, NOISE_SD_M, size=profile.shape)
    return profile + noise, profile


@pytest.mark.parametrize("statistic", ["median", "mean"])
def test_expected_recovers_the_weekly_profile(statistic: Statistic) -> None:
    observed, profile = synthetic_year()
    oof = oof_hour_of_week_residuals(observed, slot_minutes=60, statistic=statistic)
    expected = observed - oof.residuals
    # ~48 training draws per slot: the error of the mean is ~0.007 m, so 0.06 m is ~8 sigma
    assert float((expected - profile).abs().to_numpy().max()) < 0.06


OFFSET_M = 1000.0  # invented, enormous next to the 0.05 m noise
HELD_OUT_MONTH = 5


def months(frame: pd.DataFrame) -> np.ndarray:
    return np.asarray(pd.DatetimeIndex(frame.index).month)


def with_offset_in_may(observed: pd.DataFrame) -> pd.DataFrame:
    shifted = observed.copy()
    shifted.loc[months(observed) == HELD_OUT_MONTH] += OFFSET_M
    return shifted


@pytest.mark.parametrize("statistic", ["median", "mean"])
def test_expected_of_a_month_does_not_use_that_month(statistic: Statistic) -> None:
    observed, _ = synthetic_year()
    in_may = months(observed) == HELD_OUT_MONTH
    base = oof_hour_of_week_residuals(observed, slot_minutes=60, statistic=statistic)
    moved = oof_hour_of_week_residuals(
        with_offset_in_may(observed), slot_minutes=60, statistic=statistic
    )
    # May moved by +OFFSET but its expected did not: its residual moved by exactly +OFFSET
    shift = moved.residuals[in_may] - base.residuals[in_may]
    np.testing.assert_allclose(shift.to_numpy(), OFFSET_M, atol=1e-9)


def test_mean_of_the_other_months_does_use_the_offset_month() -> None:
    observed, _ = synthetic_year()
    not_may = months(observed) != HELD_OUT_MONTH
    base = oof_hour_of_week_residuals(observed, slot_minutes=60, statistic="mean")
    moved = oof_hour_of_week_residuals(
        with_offset_in_may(observed), slot_minutes=60, statistic="mean"
    )
    # May is ~4 of the ~48 training draws of every other month's slot: its expected rises by ~90
    shift = moved.residuals[not_may] - base.residuals[not_may]
    assert float(shift.max().max()) < -50.0


@pytest.mark.parametrize("slot_minutes", [0, -60, 7, 25, 2880])
def test_slot_minutes_must_divide_a_day(slot_minutes: int) -> None:
    observed, _ = synthetic_year()
    with pytest.raises(ValueError, match="slot_minutes"):
        oof_hour_of_week_residuals(observed, slot_minutes=slot_minutes, statistic="median")


def test_unknown_statistic_is_rejected() -> None:
    observed, _ = synthetic_year()
    with pytest.raises(ValueError, match="statistic"):
        oof_hour_of_week_residuals(
            observed,
            slot_minutes=60,
            statistic="max",  # type: ignore[arg-type]
        )


def test_slot_without_training_data_is_rejected() -> None:
    observed, _ = synthetic_year()
    only_march = observed[months(observed) == 3]  # every slot is held out and none is left
    with pytest.raises(ValueError, match="training"):
        oof_hour_of_week_residuals(only_march, slot_minutes=60, statistic="median")


def test_result_keeps_the_shape_and_names_the_fold_of_each_window() -> None:
    observed, _ = synthetic_year()
    oof = oof_hour_of_week_residuals(observed, slot_minutes=120, statistic="mean")
    pd.testing.assert_index_equal(oof.residuals.index, observed.index)
    pd.testing.assert_index_equal(oof.residuals.columns, observed.columns)
    assert list(oof.fold.index) == list(observed.index)
    assert oof.fold.tolist() == months(observed).tolist()  # fold = calendar month 1..12
    assert (oof.slot_minutes, oof.statistic) == (120, "mean")


def broken_inputs() -> dict[str, tuple[pd.DataFrame, str]]:
    """Name -> (table, text the error must mention)."""
    observed, _ = synthetic_year()
    previous = observed.copy()
    previous.index = pd.DatetimeIndex(previous.index) - pd.DateOffset(years=1)  # invented 2017
    with_nan = observed.copy()
    with_nan.iloc[10, 1] = np.nan
    with_inf = observed.copy()
    with_inf.iloc[10, 1] = np.inf
    return {
        "positional index": (observed.reset_index(drop=True), "DatetimeIndex"),
        "timezone-aware index": (observed.tz_localize("UTC"), "timezone"),
        "more than one calendar year": (pd.concat([previous, observed]), "one calendar year"),
        "NaN": (with_nan, "finite"),
        "infinity": (with_inf, "finite"),
    }


@pytest.mark.parametrize("name", list(broken_inputs()))
def test_input_must_be_a_finite_naive_datetime_table_of_one_year(name: str) -> None:
    table, message = broken_inputs()[name]
    with pytest.raises(ValueError, match=message):
        oof_hour_of_week_residuals(table, slot_minutes=60, statistic="median")


def test_residual_series_lists_each_sensor_in_column_order() -> None:
    index = pd.date_range("2018-01-01 00:00", periods=2, freq="5min", name="timestamp")
    oof = OofResiduals(
        residuals=pd.DataFrame({"n2": [1.0, -2.0], "n1": [0.5, 0.25]}, index=index),
        fold=pd.Series([1, 1], index=index, name="fold"),
        slot_minutes=60,
        statistic="median",
    )
    series = residual_series(oof)
    assert series == {"n2": [1.0, -2.0], "n1": [0.5, 0.25]}
    assert list(series) == ["n2", "n1"]  # calibrate() keeps the order of the mapping


RESIDUALS_FILE = "residuals.csv.gz"
FOLDS_FILE = "folds.json"


def synthetic_oof(slot_minutes: int = 60, statistic: Statistic = "median") -> OofResiduals:
    observed, _ = synthetic_year()
    return oof_hour_of_week_residuals(observed, slot_minutes=slot_minutes, statistic=statistic)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_write_oof_is_deterministic_and_returns_the_sha256_of_the_files(tmp_path: Path) -> None:
    oof = synthetic_oof()
    first = write_oof(oof, tmp_path / "first")
    second = write_oof(oof, tmp_path / "second")
    assert first == second
    for out in ("first", "second"):
        assert (
            sha256_of(tmp_path / out / RESIDUALS_FILE),
            sha256_of(tmp_path / out / FOLDS_FILE),
        ) == first
    assert (tmp_path / "first" / RESIDUALS_FILE).read_bytes() == (
        tmp_path / "second" / RESIDUALS_FILE
    ).read_bytes()


def test_gzip_header_carries_neither_a_timestamp_nor_a_file_name(tmp_path: Path) -> None:
    write_oof(synthetic_oof(), tmp_path)
    header = (tmp_path / RESIDUALS_FILE).read_bytes()[:10]
    assert header[:2] == b"\x1f\x8b"  # gzip magic
    assert header[3] == 0  # flags: no FNAME
    assert header[4:8] == b"\x00\x00\x00\x00"  # mtime = 0


@pytest.mark.parametrize("statistic", ["median", "mean"])
def test_residuals_file_reproduces_the_floats_exactly(tmp_path: Path, statistic: Statistic) -> None:
    oof = synthetic_oof(statistic=statistic)
    write_oof(oof, tmp_path)
    reread = pd.read_csv(
        tmp_path / RESIDUALS_FILE,
        index_col="timestamp",
        parse_dates=True,
        float_precision="round_trip",
    )
    assert list(reread.columns) == list(SENSORS)
    assert reread.index.equals(oof.residuals.index)
    assert (reread.to_numpy() == oof.residuals.to_numpy()).all()  # bit for bit, not allclose


def test_folds_file_defines_each_month_as_a_block_of_time(tmp_path: Path) -> None:
    write_oof(synthetic_oof(slot_minutes=120, statistic="mean"), tmp_path)
    text = (tmp_path / FOLDS_FILE).read_text(encoding="utf-8")
    expected_folds = {
        f"{month:02d}": [
            f"2018-{month:02d}-01T00:00:00",
            f"2018-{month:02d}-{calendar.monthrange(2018, month)[1]:02d}T23:00:00",
        ]
        for month in range(1, 13)
    }
    assert json.loads(text) == {"folds": expected_folds, "slot_minutes": 120, "statistic": "mean"}
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"  # canonical


def test_folds_hash_changes_with_the_definition_of_the_baseline(tmp_path: Path) -> None:
    oof = synthetic_oof()
    base = write_oof(oof, tmp_path / "base")[1]
    other_slot = write_oof(dataclasses.replace(oof, slot_minutes=120), tmp_path / "slot")[1]
    other_statistic = write_oof(dataclasses.replace(oof, statistic="mean"), tmp_path / "stat")[1]
    assert len({base, other_slot, other_statistic}) == 3


def test_written_hashes_and_series_feed_calibrate(tmp_path: Path) -> None:
    oof = synthetic_oof()
    residuals_sha256, folds_sha256 = write_oof(oof, tmp_path)
    artefact = calibrate(
        residual_series(oof),
        provenance="dev-baseline-2018",
        forecaster=ForecasterIdentity(name="hour-of-week-baseline", version="0.0.0"),
        residuals_sha256=residuals_sha256,
        folds_sha256=folds_sha256,
        # invented parameters, only to exercise the interface
        sigma_floor_m=0.01,
        k=0.5,
        h=8.0,
        n_min=1,
        c=2.0,
        h_close=4.0,
    )
    assert list(artefact.sigma_m) == list(SENSORS)
    assert (artefact.residuals_sha256, artefact.folds_sha256) == (residuals_sha256, folds_sha256)
    assert artefact.is_mock  # a development calibration is never final


# ---------- loading 2018 from the sealed data version (fake files here; real ones below) ----------

SCADA_FILE = "scada_2018.csv.gz"


def fake_registry(*pressure: str) -> SensorRegistry:
    return SensorRegistry(
        pressure=pressure, flow=("p227",), tank_level=("T1",), provisional=True, source="tests"
    )


def write_fake_v1(root: Path, *, timestamps: list[str] | None = None, **extra: list[float]) -> Path:
    """An invented ``<root>/v1`` with a 4-row SCADA table and a MANIFEST that matches it.

    Columns follow the SCADA naming (``Pressure_n1``...), in an order that is not the order of
    the registry used with it. ``extra`` adds or overrides columns.
    """
    v1 = root / "v1"
    v1.mkdir()
    stamps = timestamps or [f"2018-01-01 00:{minute:02d}:00" for minute in (0, 5, 10, 15)]
    frame = pd.DataFrame(
        {
            "Pressure_n1": [50.0, 51.0, 52.0, 53.0],
            "Pressure_n2": [40.0, 41.0, 42.0, 43.0],
            "Pressure_n3": [30.0, 31.0, 32.0, 33.0],
            "Flow_p227": [100.0, 101.0, 102.0, 103.0],
            "Level_T1": [3.0, 3.1, 3.2, 3.3],
            **extra,
        },
        index=pd.Index(stamps, name="timestamp"),
    )
    frame.to_csv(v1 / SCADA_FILE)
    manifest = {"files": {SCADA_FILE: {"sha256": sha256_of(v1 / SCADA_FILE)}}}
    (v1 / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_load_returns_the_pressures_named_and_ordered_like_the_registry(tmp_path: Path) -> None:
    data_dir = write_fake_v1(tmp_path)
    pressures = load_pressures_2018(data_dir, fake_registry("n3", "n1", "n2"))
    assert list(pressures.columns) == ["n3", "n1", "n2"]  # no flow, no level, registry order
    assert pressures["n3"].tolist() == [30.0, 31.0, 32.0, 33.0]
    assert pressures["n1"].tolist() == [50.0, 51.0, 52.0, 53.0]
    assert isinstance(pressures.index, pd.DatetimeIndex)
    assert pressures.index.tz is None
    assert pressures.index[1] == pd.Timestamp("2018-01-01 00:05:00")


def test_load_refuses_a_file_that_differs_from_the_manifest_before_parsing(tmp_path: Path) -> None:
    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / SCADA_FILE).write_bytes(b"this is not a gzip file, so parsing it could not succeed")
    manifest = {"files": {SCADA_FILE: {"sha256": "0" * 64}}}  # invented, differs from the file
    (v1 / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="sha256"):
        load_pressures_2018(tmp_path, fake_registry("n1"))


def test_load_refuses_a_manifest_without_the_2018_file(tmp_path: Path) -> None:
    data_dir = write_fake_v1(tmp_path)
    (data_dir / "v1" / "MANIFEST.json").write_text(json.dumps({"files": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="MANIFEST"):
        load_pressures_2018(data_dir, fake_registry("n1"))


@pytest.mark.parametrize(
    ("registry_ids", "message"),
    [
        (("n1", "n2", "n3", "n4"), "missing.*n4"),  # in the registry, absent from the file
        (("n1", "n2"), "not in the registry.*n3"),  # in the file, absent from the registry
    ],
)
def test_load_refuses_a_file_whose_pressure_sensors_differ_from_the_registry(
    tmp_path: Path, registry_ids: tuple[str, ...], message: str
) -> None:
    data_dir = write_fake_v1(tmp_path)
    with pytest.raises(ValueError, match=message):
        load_pressures_2018(data_dir, fake_registry(*registry_ids))


@pytest.mark.parametrize(
    ("timestamps", "message"),
    [
        (["not", "a", "date", "time"], "DatetimeIndex"),
        ([f"2018-01-01 00:{m:02d}:00+00:00" for m in (0, 5, 10, 15)], "timezone"),
    ],
)
@pytest.mark.filterwarnings("ignore:Could not infer format")  # pandas, for the 'not a date' case
def test_load_requires_naive_datetime_stamps(
    tmp_path: Path, timestamps: list[str], message: str
) -> None:
    data_dir = write_fake_v1(tmp_path, timestamps=timestamps)
    with pytest.raises(ValueError, match=message):
        load_pressures_2018(data_dir, fake_registry("n1", "n2", "n3"))


# ---------- real 2018 data (skipped without SENTINEL_DATA_DIR, like test_p1_data.py) ----------

REGISTRY_FILE = Path(__file__).resolve().parents[2] / "config" / "sensors.battledim.json"
DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/does-not-exist")).expanduser()
needs_data = pytest.mark.skipif(
    not (DATA_DIR / "v1" / "MANIFEST.json").exists(), reason="data v1 unavailable"
)


@needs_data
def test_real_2018_loads_and_gives_finite_out_of_fold_residuals() -> None:
    registry = SensorRegistry.load(REGISTRY_FILE)
    assert not registry.provisional  # the official registry, not the placeholder one
    pressures = load_pressures_2018(DATA_DIR, registry)
    assert pressures.shape == (105_120, 33)  # 365 days x 288 windows, one column per sensor
    assert list(pressures.columns) == list(registry.pressure)
    assert pressures.index[0] == pd.Timestamp("2018-01-01 00:00")
    assert pressures.index[-1] == pd.Timestamp("2018-12-31 23:55")

    oof = oof_hour_of_week_residuals(pressures, slot_minutes=60, statistic="median")

    assert oof.residuals.shape == pressures.shape
    assert set(oof.fold.tolist()) == set(range(1, 13))
    assert np.isfinite(oof.residuals.to_numpy()).all()
