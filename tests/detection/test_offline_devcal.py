"""Development calibration pipeline on 2018 (BE-012f, ADR-008 §C, §E and §H).

The structure and sigma-sensitivity functions are tested with synthetic signals whose answer is
known (hand-worked small cases and seeded ``numpy`` noise); every number is invented and none
is a result. ``run`` and the CLI are tested on a **fake, small** ``v1/`` directory built in
``tmp_path`` (invented pressures, invented leaks, the 33 sensors of the official registry, a
coarse hourly grid over January and February so every slot of the baseline has two folds).
The real-data test is skipped without ``SENTINEL_DATA_DIR`` (same pattern as
``tests/test_p1_data.py``) and asserts that the files exist and that the artefact is valid,
never a result value.
"""

import gzip
import hashlib
import inspect
import json
import math
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from sentinel.detection.calibration import (
    CalibrationArtefact,
    ForecasterIdentity,
    calibrate,
)
from sentinel.detection.cusum import CusumDetector
from sentinel.detection.offline import devcal
from sentinel.detection.offline.baseline import (
    OofResiduals,
    oof_hour_of_week_residuals,
    residual_series,
)
from sentinel.detection.offline.layer1 import MatchRule
from sentinel.detection.offline.sweep import sweep
from sentinel.sensors import SensorRegistry

REGISTRY_FILE = Path(__file__).resolve().parents[2] / "config" / "sensors.battledim.json"
ARTEFACT_FILE = "calibration.dev-baseline-2018.json"
OUTPUT_FILES = (
    "residuals.csv.gz",
    "folds.json",
    ARTEFACT_FILE,
    "sweep.csv",
    "report.md",
)


# --- 1. Residual structure, on signals with a known answer ---------------------------------


def frame(**columns: list[float]) -> pd.DataFrame:
    return pd.DataFrame(columns)


def test_lag1_autocorrelation_of_hand_worked_series() -> None:
    # x = 1, 2, 3, 4: deviations -1.5, -.5, .5, 1.5; sum of lag-1 products 1.25; sum of squares 5
    ramp = frame(ramp=[1.0, 2.0, 3.0, 4.0])
    alternating = frame(alternating=[1.0, -1.0] * 5)  # -(n - 1) / n for n = 10

    assert devcal.lag1_autocorrelation(ramp)["ramp"] == pytest.approx(0.25)
    assert devcal.lag1_autocorrelation(alternating)["alternating"] == pytest.approx(-0.9)


def test_lag1_autocorrelation_recovers_an_ar1_coefficient_per_sensor() -> None:
    rng = np.random.default_rng(1)
    n, phi = 20_000, 0.8
    shocks = rng.normal(size=n)
    ar1 = np.empty(n)
    ar1[0] = shocks[0]
    for t in range(1, n):
        ar1[t] = phi * ar1[t - 1] + shocks[t]
    table = pd.DataFrame({"ar1": ar1, "white": rng.normal(size=n)})

    r1 = devcal.lag1_autocorrelation(table)

    assert list(r1.index) == ["ar1", "white"]
    assert r1["ar1"] == pytest.approx(phi, abs=0.03)
    assert abs(r1["white"]) < 0.03


@pytest.mark.parametrize(
    "function",
    [
        devcal.lag1_autocorrelation,
        devcal.common_mode_correlation,
        devcal.pairwise_correlation,
    ],
)
def test_a_constant_sensor_has_no_correlation(function: Callable[[pd.DataFrame], object]) -> None:
    table = frame(a=[1.0, 2.0, 3.0, 4.0], flat=[5.0, 5.0, 5.0, 5.0])
    with pytest.raises(ValueError, match="flat"):
        function(table)


def hourly_scale_frame(days: int, scale_by_hour: dict[int, float], seed: int) -> pd.DataFrame:
    index = pd.date_range("2018-01-01 00:05", periods=days * 288, freq="5min", name="timestamp")
    scale = np.array([scale_by_hour[h] for h in index.hour])
    noise = np.random.default_rng(seed).normal(size=(len(index), 2))
    return pd.DataFrame(noise * scale[:, None], index=index, columns=["a", "b"])


def test_diurnal_robust_scale_of_a_hand_worked_hour() -> None:
    # one hour, values -1, 0, 1, 2, 10: median 1, absolute deviations 2, 1, 0, 1, 9 -> MAD 1
    index = pd.date_range("2018-01-01 03:05", periods=5, freq="5min")
    table = pd.DataFrame({"a": [-1.0, 0.0, 1.0, 2.0, 10.0]}, index=index)

    scale = devcal.diurnal_robust_scale(table)

    assert list(scale.index) == [3]
    assert scale.index.name == "hour"
    assert scale.loc[3, "a"] == pytest.approx(1.4826)  # 1.4826 * MAD


def test_diurnal_robust_scale_follows_a_day_night_change_of_scale() -> None:
    by_hour = {h: (1.0 if h < 12 else 3.0) for h in range(24)}
    table = hourly_scale_frame(days=60, scale_by_hour=by_hour, seed=2)

    scale = devcal.diurnal_robust_scale(table)

    assert list(scale.index) == list(range(24))
    for hour, true_scale in by_hour.items():
        assert scale.loc[hour].to_numpy() == pytest.approx(true_scale, rel=0.15)  # ~720 draws/hour
    ratio = devcal.diurnal_scale_ratio(scale, sigma_floor_m=0.01)
    assert ratio.to_numpy() == pytest.approx(3.0, rel=0.3)


def test_diurnal_scale_ratio_is_largest_over_smallest_hour_per_sensor() -> None:
    scale = pd.DataFrame({"a": [1.0, 3.0, 2.0], "b": [4.0, 4.0, 4.0]}, index=[0, 1, 2])

    ratio = devcal.diurnal_scale_ratio(scale, sigma_floor_m=0.01)

    assert ratio.to_dict() == {"a": 3.0, "b": 1.0}


def test_diurnal_scale_ratio_floors_the_scale_like_sigma_so_it_stays_finite() -> None:
    # a quantised sensor can have MAD = 0 in some hour: without the floor the ratio is infinite
    scale = pd.DataFrame({"a": [0.0, 2.0, 1.0], "b": [0.0, 0.2, 0.1]}, index=[0, 1, 2])

    ratio = devcal.diurnal_scale_ratio(scale, sigma_floor_m=0.5)

    assert ratio.to_dict() == {"a": 4.0, "b": 1.0}  # 2 / 0.5 and 0.5 / 0.5


@pytest.mark.parametrize("floor", [0.0, -0.1, math.inf, math.nan])
def test_diurnal_scale_floor_must_be_positive_and_finite(floor: float) -> None:
    with pytest.raises(ValueError, match="sigma_floor_m"):
        devcal.diurnal_scale_ratio(pd.DataFrame({"a": [1.0, 2.0]}), sigma_floor_m=floor)


def test_pairwise_correlation_of_a_hand_worked_case() -> None:
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    table = pd.DataFrame({"a": a, "b": [2 * x + 1 for x in a], "c": [-x for x in a]})
    # corr(a, b) = 1, corr(a, c) = corr(b, c) = -1: median -1, maximum 1 at the pair (a, b)

    result = devcal.pairwise_correlation(table)

    assert result.median == pytest.approx(-1.0)
    assert result.maximum == pytest.approx(1.0)
    assert result.maximum_pair == ("a", "b")
    assert result.pairs == 3


def test_common_mode_correlation_is_the_correlation_with_the_median_across_sensors() -> None:
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    table = pd.DataFrame({"a": a, "b": [2 * x + 1 for x in a], "c": [-x for x in a]})
    # the row-wise median of (x, 2x + 1, -x) is x, i.e. column a

    corr = devcal.common_mode_correlation(table)

    assert corr.to_dict() == pytest.approx({"a": 1.0, "b": 1.0, "c": -1.0})


def sensors_with_common_mode(share: float, *, sensors: int = 33, windows: int = 3000) -> Any:
    """Each sensor = sqrt(share) * common + sqrt(1 - share) * own noise: pairwise corr = share."""
    rng = np.random.default_rng(3)
    common = rng.normal(size=(windows, 1))
    own = rng.normal(size=(windows, sensors))
    return pd.DataFrame(math.sqrt(share) * common + math.sqrt(1.0 - share) * own)


def test_a_shared_component_shows_in_both_correlation_measures() -> None:
    independent = sensors_with_common_mode(0.0)
    shared = sensors_with_common_mode(0.5)

    pair_independent = devcal.pairwise_correlation(independent)
    pair_shared = devcal.pairwise_correlation(shared)

    assert abs(pair_independent.median) < 0.05
    assert pair_shared.median == pytest.approx(0.5, abs=0.05)
    assert pair_shared.maximum < 0.65
    assert devcal.common_mode_correlation(independent).mean() < 0.3
    assert devcal.common_mode_correlation(shared).mean() > 0.6


def test_extreme_rate_counts_strictly_above_the_threshold_in_either_direction() -> None:
    z = frame(a=[0.0, 4.0, -4.5, 5.0, 0.1, 0.2, -0.3, 0.4, 3.9, -3.9], b=[0.0] * 10)

    rate = devcal.extreme_rate(z, threshold=4.0)  # |z| = 4.0 is not > 4

    assert rate.to_dict() == {"a": 0.2, "b": 0.0}


@pytest.mark.parametrize("threshold", [0.0, -1.0, math.inf, math.nan])
def test_extreme_threshold_must_be_positive_and_finite(threshold: float) -> None:
    with pytest.raises(ValueError, match="threshold"):
        devcal.extreme_rate(frame(a=[1.0, 2.0]), threshold=threshold)


def test_gaussian_reference_rate_is_the_two_sided_tail() -> None:
    assert devcal.gaussian_extreme_rate(4.0) == pytest.approx(6.334248366623993e-05, rel=1e-9)
    assert devcal.gaussian_extreme_rate(1.959963984540054) == pytest.approx(0.05, rel=1e-6)


def test_range_summary_gives_min_median_max_and_the_sensors_that_hold_them() -> None:
    structure = pd.DataFrame(
        {"lag1_autocorr": [0.5, 0.9, 0.7], "extreme_rate": [0.0, 0.1, 0.01]},
        index=["s1", "s2", "s3"],
    )

    ranges = devcal.range_summary(structure)

    assert ranges.index.name == "statistic"

    assert list(ranges.loc["lag1_autocorr", ["min", "median", "max"]]) == [0.5, 0.7, 0.9]
    assert list(ranges.loc["lag1_autocorr", ["min_sensor", "max_sensor"]]) == ["s1", "s2"]
    assert list(ranges.loc["extreme_rate", ["min_sensor", "max_sensor"]]) == ["s1", "s2"]


# --- 2. Sensitivity of sigma to the leak labels --------------------------------------------

SENSITIVITY_INDEX = pd.date_range("2018-01-01 00:05", periods=7, freq="5min", name="timestamp")


def sensitivity_inputs() -> tuple[pd.DataFrame, pd.Series]:
    """Sensor ``a``: clean windows -1, 0, 1 (MAD 1) and leak windows -10, -10, 10, 10 (all
    seven: median 0, MAD 10). Sensor ``b``: tiny everywhere, so only the floor matters."""
    residuals = pd.DataFrame(
        {
            "a": [-10.0, -1.0, 10.0, 0.0, 1.0, -10.0, 10.0],
            "b": [0.001, -0.001, 0.002, -0.002, 0.001, -0.001, 0.002],
        },
        index=SENSITIVITY_INDEX,
    )
    clean = pd.Series(
        [False, True, False, True, True, False, False], index=SENSITIVITY_INDEX, name="limpo"
    )
    return residuals, clean


def test_sigma_sensitivity_compares_all_windows_with_the_clean_ones() -> None:
    residuals, clean = sensitivity_inputs()

    table = devcal.sigma_sensitivity(residuals, clean, sigma_floor_m=0.5)

    assert list(table.columns) == ["sigma_all", "sigma_clean", "ratio"]
    assert table.index.name == "sensor"
    assert table.loc["a", "sigma_all"] == pytest.approx(14.826)  # 1.4826 * 10
    assert table.loc["a", "sigma_clean"] == pytest.approx(1.4826)  # 1.4826 * 1
    assert table.loc["a", "ratio"] == pytest.approx(10.0)
    assert list(table.loc["b"]) == [0.5, 0.5, 1.0]  # the floor decides both


def calibrated_sigma(residuals: pd.DataFrame) -> dict[str, float]:
    """The sigma ``calibrate`` estimates for a residual table (invented parameters besides the
    floor, which are irrelevant to sigma)."""
    fold = pd.Series(1, index=residuals.index, name="fold")
    artefact = calibrate(
        residual_series(OofResiduals(residuals, fold, 60, "median")),
        provenance="dev-baseline-2018",
        forecaster=ForecasterIdentity(name="x", version="0"),
        residuals_sha256="0" * 64,
        folds_sha256="0" * 64,
        sigma_floor_m=0.5,
        k=0.5,
        h=8.0,
        n_min=1,
        c=2.0,
        h_close=4.0,
    )
    return dict(artefact.sigma_m)


def test_sigma_sensitivity_estimates_sigma_as_calibrate_does() -> None:
    residuals, clean = sensitivity_inputs()

    table = devcal.sigma_sensitivity(residuals, clean, sigma_floor_m=0.5)

    assert table["sigma_all"].to_dict() == pytest.approx(calibrated_sigma(residuals))
    assert table["sigma_clean"].to_dict() == pytest.approx(calibrated_sigma(residuals[clean]))


def test_sigma_sensitivity_needs_a_clean_window_and_a_mask_of_the_same_windows() -> None:
    residuals, clean = sensitivity_inputs()
    with pytest.raises(ValueError, match="no clean window"):
        devcal.sigma_sensitivity(residuals, clean & False, sigma_floor_m=0.5)
    with pytest.raises(ValueError, match="same windows"):
        devcal.sigma_sensitivity(residuals, clean.iloc[:-1], sigma_floor_m=0.5)
    shifted = clean.set_axis(clean.index + pd.Timedelta(minutes=5))
    with pytest.raises(ValueError, match="same windows"):
        devcal.sigma_sensitivity(residuals, shifted, sigma_floor_m=0.5)


@pytest.mark.parametrize("floor", [0.0, -0.1, math.inf, math.nan])
def test_sigma_sensitivity_floor_must_be_positive_and_finite(floor: float) -> None:
    residuals, clean = sensitivity_inputs()
    with pytest.raises(ValueError, match="sigma_floor_m"):
        devcal.sigma_sensitivity(residuals, clean, sigma_floor_m=floor)


# --- 3. The sweep summary ------------------------------------------------------------------

STEP_START = 40


def step_sweep() -> pd.DataFrame:
    """A 2-sensor ``z`` with a step on both sensors at window 40 and one abrupt leak there,
    plus an incipient leak that nothing reveals."""
    index = pd.date_range("2018-01-01 00:05", periods=100, freq="5min", name="timestamp")
    z = np.zeros((100, 2))
    z[STEP_START:] = 5.0
    leaks = pd.DataFrame(
        {
            "pipe": ["pA", "pB"],
            "start": [index[STEP_START], index[10]],
            "end": [index[90], index[20]],
            "type": ["abrupt", "incipient"],
            "peak": [index[STEP_START], index[15]],
        }
    )
    return sweep(
        z,
        index,
        leaks,
        k=0.5,
        h_values=[8.0, 16.0],
        n_values=[1, 2],
        c=2.0,
        h_close=4.0,
        match=MatchRule({"abrupt": timedelta(hours=1), "incipient": timedelta(hours=1)}),
    )


def test_summarise_sweep_gives_counts_delays_and_missed_leaks_per_configuration() -> None:
    tables = devcal.summarise_sweep(step_sweep())

    assert set(tables) == {"counts", "delays_h", "missed"}
    counts = tables["counts"]
    assert counts[["h", "N"]].values.tolist() == [[8.0, 1], [8.0, 2], [16.0, 1], [16.0, 2]]
    assert counts["abrupt"].tolist() == ["1/1"] * 4
    assert counts["incipient"].tolist() == ["0/1"] * 4
    assert {"openings", "false_alarms", "false_alarms_per_week", "repeat_openings"} <= set(
        counts.columns
    )
    delays = tables["delays_h"]
    assert list(delays.columns) == [
        "h",
        "N",
        "start_abrupt",
        "start_incipient",
        "peak_abrupt",
        "peak_incipient",
    ]
    # S gains 4.5 per window from the step: above h = 8 at the 2nd window (5 min after the
    # first), above h = 16 at the 4th (15 min after it)
    assert delays["start_abrupt"].tolist() == pytest.approx([5 / 60, 5 / 60, 15 / 60, 15 / 60])
    assert delays["start_incipient"].isna().all()  # nothing detected: no delay
    missed = tables["missed"]
    assert missed["abrupt"].tolist() == ["-"] * 4
    assert missed["incipient"].tolist() == ["pB"] * 4


# --- 4. run() on a fake data directory -----------------------------------------------------

SENSORS = SensorRegistry.load(REGISTRY_FILE).pressure
FAKE_NOISE_SD_M = 0.05  # invented
FAKE_STEP_M = -2.0  # invented: pressure drop on the first STEPPED sensors
STEPPED = 5
INTERVAL = timedelta(hours=1)  # the fake grid is hourly; real windows are 5 minutes


def fake_pressures() -> pd.DataFrame:
    index = pd.date_range("2018-01-01 00:00", "2018-02-28 23:00", freq="1h", name="timestamp")
    hour_of_week = np.asarray(index.dayofweek * 24 + index.hour, dtype=float)
    base = np.stack(
        [40.0 + i + 2.0 * np.sin(2.0 * np.pi * hour_of_week / 168.0) for i in range(len(SENSORS))],
        axis=1,
    )
    noise = np.random.default_rng(20180101).normal(0.0, FAKE_NOISE_SD_M, size=base.shape)
    values = np.round(base + noise, 2)  # two decimals like the real file: the CSV round-trips
    step = np.asarray((index >= pd.Timestamp("2018-02-10")) & (index <= pd.Timestamp("2018-02-12")))
    values[np.ix_(step, np.arange(STEPPED))] += FAKE_STEP_M
    return pd.DataFrame(values, index=index, columns=[f"Pressure_{s}" for s in SENSORS])


def fake_leaks() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pipe": ["pI1", "pAb", "pI2"],
            "start": ["2018-01-15 00:00:00", "2018-02-10 00:00:00", "2018-01-08 13:00:00"],
            "end": ["2018-01-20 00:00:00", "2018-02-12 00:00:00", "2018-12-31 23:55:00"],
            "leak_size": [0.05, 0.1, 0.02],
            "type": ["incipient", "abrupt", "incipient"],
            "peak": ["2018-01-18 00:00:00", "2018-02-10 00:00:00", "2018-01-25 00:00:00"],
            "repaired_in_2018": [True, True, False],
        }
    )


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_fake_data_dir(root: Path) -> Path:
    """``<root>/v1`` with the 2018 SCADA table, the 2018 labels and a MANIFEST that matches
    them. A 2019 file with garbage inside is planted to prove nothing reads it."""
    v1 = root / "v1"
    v1.mkdir(parents=True)
    pressures = fake_pressures()
    pressures["Flow_p227"] = 100.0  # the loader ignores everything but the pressure columns
    pressures["Level_T1"] = 3.0
    pressures.to_csv(v1 / "scada_2018.csv.gz")
    fake_leaks().to_csv(v1 / "leaks_2018.csv", index=False)
    (v1 / "scada_2019.csv.gz").write_bytes(b"planted: reading this must fail")
    manifest = {
        "files": {
            name: {"sha256": sha256_of(v1 / name)}
            for name in ("scada_2018.csv.gz", "leaks_2018.csv", "scada_2019.csv.gz")
        }
    }
    (v1 / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def fake_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_fake_data_dir(tmp_path_factory.mktemp("fake-data"))


def fake_match() -> MatchRule:
    return MatchRule({"abrupt": timedelta(hours=12), "incipient": timedelta(hours=48)})


def run_arguments(**overrides: Any) -> dict[str, Any]:
    """Invented development numbers for the fake data: the step is ~40 sigma, so h and N only
    need to be reachable (5 sensors step together)."""
    arguments: dict[str, Any] = {
        "slot_minutes": 60,
        "statistic": "median",
        "sigma_floor_m": 0.01,
        "k": 0.5,
        "h_values": (8.0, 12.0),
        "n_values": (1, 3),
        "c": 2.0,
        "h_close_ratio": 0.5,
        "artefact_h": 8.0,
        "artefact_n": 3,
        "match": fake_match(),
    }
    return {**arguments, **overrides}


@pytest.fixture(scope="module")
def fake_report(
    fake_data_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> devcal.DevCalReport:
    out_dir = tmp_path_factory.mktemp("fake-out") / "dev-baseline-2018"
    return devcal.run(fake_data_dir, out_dir, **run_arguments())


class OpenTrace:
    """Records every file the interpreter opens while ``active`` (``sys.addaudithook`` cannot be
    undone, so the hook is installed once and stays inert when not tracing)."""

    def __init__(self) -> None:
        self.installed = False
        self.active = False
        self.paths: list[str] = []

    def record(self, event: str, args: tuple[Any, ...]) -> None:
        if self.active and event == "open":
            self.paths.append(str(args[0]))

    @contextmanager
    def tracing(self) -> Iterator[list[str]]:
        if not self.installed:
            sys.addaudithook(self.record)
            self.installed = True
        self.paths = []
        self.active = True
        try:
            yield self.paths
        finally:
            self.active = False


OPEN_TRACE = OpenTrace()


def test_run_opens_no_2019_file(tmp_path: Path) -> None:
    data_dir = write_fake_data_dir(tmp_path / "data")

    with OPEN_TRACE.tracing() as opened:
        devcal.run(data_dir, tmp_path / "out", **run_arguments())

    assert any(path.endswith("scada_2018.csv.gz") for path in opened)  # the trace sees reads
    in_data_dir = [Path(path) for path in opened if Path(path).is_relative_to(data_dir)]
    assert [path.name for path in in_data_dir if "2019" in path.name] == []
    assert {path.name for path in in_data_dir} >= {"scada_2018.csv.gz", "leaks_2018.csv"}


def test_run_writes_every_output_file(fake_report: devcal.DevCalReport) -> None:
    out_dir = fake_report.out_dir
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(OUTPUT_FILES)
    assert fake_report.residuals_path == out_dir / "residuals.csv.gz"
    assert fake_report.folds_path == out_dir / "folds.json"
    assert fake_report.artefact_path == out_dir / ARTEFACT_FILE
    assert fake_report.sweep_path == out_dir / "sweep.csv"
    assert fake_report.report_path == out_dir / "report.md"


def test_artefact_is_a_development_calibration_the_detector_loads(
    fake_report: devcal.DevCalReport,
) -> None:
    detector = CusumDetector.from_file(fake_report.artefact_path, interval=INTERVAL)

    assert detector.info.is_mock  # a development calibration is never final
    artefact = CalibrationArtefact.model_validate_json(fake_report.artefact_path.read_bytes())
    assert artefact == fake_report.artefact
    assert artefact.provenance == "dev-baseline-2018"
    assert artefact.forecaster == ForecasterIdentity(
        name="p4-dev-hour-of-week", version="0.1.0+s60-median"
    )
    assert (artefact.k, artefact.h, artefact.n_min, artefact.c) == (0.5, 8.0, 3, 2.0)
    assert artefact.h_close == 0.5 * 8.0  # h_close_ratio * artefact_h
    assert list(artefact.sigma_m) == list(SENSORS)  # the registry order, as the detector needs


def test_forecaster_version_fits_the_32_character_limit() -> None:
    identity = devcal.forecaster_identity(slot_minutes=1440, statistic="median")
    assert identity.name == "p4-dev-hour-of-week"
    assert identity.version == "0.1.0+s1440-median"
    assert len(devcal.forecaster_identity(slot_minutes=10**20, statistic="median").version) <= 32


def test_hashes_are_those_of_the_files_written(fake_report: devcal.DevCalReport) -> None:
    content = gzip.decompress(fake_report.residuals_path.read_bytes())
    assert fake_report.residuals_sha256 == hashlib.sha256(content).hexdigest()
    assert fake_report.folds_sha256 == sha256_of(fake_report.folds_path)
    assert fake_report.artefact_sha256 == sha256_of(fake_report.artefact_path)
    assert fake_report.artefact.residuals_sha256 == fake_report.residuals_sha256
    assert fake_report.artefact.folds_sha256 == fake_report.folds_sha256


def fake_oof() -> OofResiduals:
    pressures = fake_pressures().rename(columns=lambda c: c.removeprefix("Pressure_"))
    return oof_hour_of_week_residuals(pressures, slot_minutes=60, statistic="median")


def test_the_artefact_is_calibrate_on_the_baseline_residuals(
    fake_report: devcal.DevCalReport,
) -> None:
    expected = calibrate(
        residual_series(fake_oof()),
        provenance="dev-baseline-2018",
        forecaster=devcal.forecaster_identity(slot_minutes=60, statistic="median"),
        residuals_sha256=fake_report.residuals_sha256,
        folds_sha256=fake_report.folds_sha256,
        sigma_floor_m=0.01,
        k=0.5,
        h=8.0,
        n_min=3,
        c=2.0,
        h_close=4.0,
    )
    assert fake_report.artefact == expected


def test_sweep_detects_the_planted_drop_and_has_one_row_per_pair(
    fake_report: devcal.DevCalReport,
) -> None:
    table = pd.read_csv(fake_report.sweep_path)

    assert table[["h", "n_min"]].values.tolist() == [[8, 1], [8, 3], [12, 1], [12, 3]]
    # the first 5 sensors drop 40 sigma at the start of the abrupt leak: z > 0 on a drop
    assert table["detected_abrupt"].tolist() == [1, 1, 1, 1]
    assert table["total_abrupt"].tolist() == [1] * 4
    assert table["total_incipient"].tolist() == [2] * 4
    assert fake_report.sweep[["h", "n_min", "detected_abrupt"]].values.tolist() == (
        table[["h", "n_min", "detected_abrupt"]].values.tolist()
    )


def test_h_close_follows_h_in_the_sweep(
    fake_report: devcal.DevCalReport, fake_data_dir: Path
) -> None:
    # every h is swept with h_close = h_close_ratio * h, so the rows of one h are the very
    # sweep that h would give alone; the artefact (h = 8) is one of those rows
    oof = fake_oof()
    sigma = np.array(list(fake_report.artefact.sigma_m.values()))
    leaks = pd.read_csv(
        fake_data_dir / "v1" / "leaks_2018.csv", parse_dates=["start", "end", "peak"]
    )
    direct = sweep(
        -oof.residuals.to_numpy() / sigma,
        pd.DatetimeIndex(oof.residuals.index),
        leaks,
        k=0.5,
        h_values=[12.0],
        n_values=[1, 3],
        c=2.0,
        h_close=0.5 * 12.0,
        match=fake_match(),
    )

    rows = fake_report.sweep[fake_report.sweep["h"] == 12.0].reset_index(drop=True)

    pd.testing.assert_frame_equal(rows, direct)


def test_sigma_sensitivity_uses_the_mask_policies_of_the_p1_data(
    fake_report: devcal.DevCalReport,
) -> None:
    assert list(fake_report.sigma_sensitivity) == ["todos", "reparados"]
    # the unrepaired leak starts on 2018-01-08 13:00: 7 days and 13 hours of 59 days are clean
    assert fake_report.clean_fraction["todos"] == pytest.approx((7 * 24 + 13) / (59 * 24), abs=1e-3)
    assert fake_report.clean_fraction["reparados"] > fake_report.clean_fraction["todos"]
    table = fake_report.sigma_sensitivity["todos"]
    assert list(table.index) == list(SENSORS)
    assert list(table.columns) == ["sigma_all", "sigma_clean", "ratio"]
    assert table["sigma_all"].to_dict() == pytest.approx(fake_report.artefact.sigma_m)


def test_structure_has_one_row_per_sensor_and_the_four_statistics(
    fake_report: devcal.DevCalReport,
) -> None:
    structure = fake_report.structure
    assert list(structure.index) == list(SENSORS)
    assert structure.index.name == "sensor"
    assert list(structure.columns) == [
        "lag1_autocorr",
        "diurnal_scale_ratio",
        "common_mode_corr",
        "extreme_rate",
    ]
    assert np.isfinite(structure.to_numpy()).all()
    assert list(fake_report.structure_ranges.index) == list(structure.columns)
    assert 0.0 <= fake_report.pairwise.maximum <= 1.0
    assert set(fake_report.diurnal_scale.index) <= set(range(24))


def test_report_carries_the_notice_the_parameters_and_the_tables(
    fake_report: devcal.DevCalReport,
) -> None:
    text = fake_report.report_path.read_text(encoding="utf-8")

    assert devcal.NOTICE in text
    assert devcal.NOTICE == (
        "simulated benchmark (BattLeDIM L-Town); thresholds fixed on 2018; development "
        "calibration (`dev-baseline-2018`, is_mock), not a delivered result; no 2019 file "
        "opened; matching rule provisional (Q-UI-06); δ/k not pre-registered"
    )
    for heading in (
        "# Development calibration on 2018",
        "## Parameters",
        "## Calibration artefact",
        "## Residual structure",
        "## Sensitivity of sigma to the leak labels",
        "## Sweep",
    ):
        assert heading in text
    for digest in (
        fake_report.residuals_sha256,
        fake_report.folds_sha256,
        fake_report.artefact_sha256,
    ):
        assert digest in text
    assert "development choice, not pre-registered" in text
    assert "todos" in text  # the mask policy used is declared
    assert "z = (expected - observed) / sigma" in text


def test_run_is_reproducible_byte_for_byte(fake_data_dir: Path, tmp_path: Path) -> None:
    first = devcal.run(fake_data_dir, tmp_path / "first", **run_arguments())
    second = devcal.run(fake_data_dir, tmp_path / "second", **run_arguments())

    for name in OUTPUT_FILES:
        assert (first.out_dir / name).read_bytes() == (second.out_dir / name).read_bytes(), name


def test_run_has_no_numeric_default() -> None:
    defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(devcal.run).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    # the only default is where the official registry lives, not a number
    assert list(defaults) == ["registry_path"]
    assert defaults["registry_path"] == devcal.OFFICIAL_REGISTRY_PATH
    assert SensorRegistry.load(devcal.OFFICIAL_REGISTRY_PATH).counts["pressure"] == 33


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"h_close_ratio": 0.0}, "h_close_ratio"),
        ({"h_close_ratio": 1.5}, "h_close_ratio"),
        ({"h_close_ratio": math.nan}, "h_close_ratio"),
        ({"artefact_h": 9.0}, "artefact_h"),  # not a row of the sweep
        ({"artefact_n": 2}, "artefact_n"),
        ({"h_values": ()}, "h_values"),
        ({"n_values": ()}, "n_values"),
    ],
)
def test_run_refuses_inconsistent_parameters_before_writing_anything(
    fake_data_dir: Path, tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    out_dir = tmp_path / "out"
    with pytest.raises(ValueError, match=message):
        devcal.run(fake_data_dir, out_dir, **run_arguments(**overrides))
    assert not out_dir.exists()


def test_run_refuses_labels_that_differ_from_the_manifest(tmp_path: Path) -> None:
    data_dir = write_fake_data_dir(tmp_path / "data")
    extra = "pX,2018-03-01 00:00:00,2018-03-02 00:00:00,0.1,abrupt,2018-03-01 00:00:00,True\n"
    with (data_dir / "v1" / "leaks_2018.csv").open("a", encoding="utf-8") as file:
        file.write(extra)

    with pytest.raises(ValueError, match=r"leaks_2018\.csv.*MANIFEST"):
        devcal.run(data_dir, tmp_path / "out", **run_arguments())


# --- 5. Command line -----------------------------------------------------------------------

CLI_ARGUMENTS: dict[str, list[str]] = {
    "--slot-minutes": ["60"],
    "--statistic": ["median"],
    "--sigma-floor-m": ["0.01"],
    "--k": ["0.5"],
    "--h": ["8", "12"],
    "--n": ["1", "3"],
    "--c": ["2"],
    "--h-close-ratio": ["0.5"],
    "--artefact-h": ["8"],
    "--artefact-n": ["3"],
    "--max-delay-abrupt-h": ["12"],
    "--max-delay-incipient-h": ["48"],
}


def cli_argv(data_dir: Path, out_dir: Path, *, without: str | None = None) -> list[str]:
    arguments = {"--data-dir": [str(data_dir)], "--out-dir": [str(out_dir)], **CLI_ARGUMENTS}
    return [
        token for flag, values in arguments.items() if flag != without for token in (flag, *values)
    ]


@pytest.mark.parametrize("flag", ["--data-dir", "--out-dir", *CLI_ARGUMENTS])
def test_cli_refuses_a_missing_argument(
    flag: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        devcal.main(cli_argv(tmp_path, tmp_path / "out", without=flag))

    assert exit_info.value.code == 2
    assert flag in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_cli_runs_the_pipeline_and_prints_where_the_files_are(
    fake_data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "out"

    status = devcal.main(cli_argv(fake_data_dir, out_dir))

    assert status == 0
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(OUTPUT_FILES)
    printed = capsys.readouterr().out
    for name in OUTPUT_FILES:
        assert str(out_dir / name) in printed
    assert sha256_of(out_dir / ARTEFACT_FILE) in printed
    # the same files as the Python call, so the CLI forwards every argument
    in_python = devcal.run(fake_data_dir, tmp_path / "python", **run_arguments())
    for name in OUTPUT_FILES:
        assert (out_dir / name).read_bytes() == (in_python.out_dir / name).read_bytes(), name


# --- 6. Real 2018 data (skipped without SENTINEL_DATA_DIR, like test_p1_data.py) -----------

DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/does-not-exist")).expanduser()
needs_data = pytest.mark.skipif(
    not (DATA_DIR / "v1" / "MANIFEST.json").exists(), reason="data v1 unavailable"
)


@needs_data
def test_real_2018_run_with_a_minimal_grid_writes_a_valid_artefact(tmp_path: Path) -> None:
    out_dir = tmp_path / "dev-baseline-2018"

    report = devcal.run(
        DATA_DIR,
        out_dir,
        slot_minutes=60,
        statistic="median",
        sigma_floor_m=0.01,
        k=0.5,
        h_values=(8.0,),
        n_values=(2,),
        c=2.0,
        h_close_ratio=0.5,
        artefact_h=8.0,
        artefact_n=2,
        match=MatchRule({"abrupt": timedelta(hours=48), "incipient": timedelta(days=30)}),
    )

    for name in OUTPUT_FILES:
        assert (out_dir / name).is_file(), name
    detector = CusumDetector.from_file(report.artefact_path, interval=timedelta(minutes=5))
    assert detector.info.is_mock
    artefact = CalibrationArtefact.model_validate_json(report.artefact_path.read_bytes())
    assert artefact.provenance == "dev-baseline-2018"
    registry = SensorRegistry.load(REGISTRY_FILE)
    assert list(artefact.sigma_m) == list(registry.pressure)
    assert len(report.sweep) == 1
