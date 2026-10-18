"""Reader of the ML owner's out-of-fold residuals (BE-012j).

Synthetic files only (``tests/detection/oof_files.py``): invented sensors, pressures and
forecaster; no number comes from data and none is a result.
"""

import gzip
import hashlib
import math
import random
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sentinel.detection.offline.oof import (
    inflation_factor,
    lag1_autocorrelation,
    load_oof_residuals,
    quantization_step,
    refuse_2019,
    z_matrix,
)
from tests.detection.oof_files import (
    FORECASTER,
    SENSORS,
    folds_text,
    long_table,
    registry,
    wide_frames,
    write_variant,
)
from tests.detection.residuals import ar1

WINDOWS = 600  # two days and two hours of 2018-01-31 .. 2018-02-02: two folds


def frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    return wide_frames(start="2018-01-31 00:00", windows=WINDOWS, seed=1, noise_m=0.02)


def same(document: dict[str, object]) -> dict[str, object]:
    return document


def write(
    root: Path,
    table: pd.DataFrame | None = None,
    *,
    declaration: Callable[[dict[str, object]], dict[str, object]] = same,
) -> Path:
    observed, expected = frames()
    rows = long_table(observed, expected) if table is None else table
    directory = root / "variant"
    folds = folds_text(pd.DatetimeIndex(observed.index))
    write_variant(directory, rows, folds=folds, declaration=declaration)
    return directory


def test_the_files_are_read_wide_in_registry_order_with_their_hashes(work: Path) -> None:
    observed, expected = frames()
    directory = write(work)

    oof = load_oof_residuals(directory, registry())

    assert list(oof.observed.columns) == list(SENSORS)
    assert oof.observed.index.equals(observed.index)
    # %.6f round trip: the observed values are centimetres, the expected ones micrometres.
    np.testing.assert_allclose(oof.observed.to_numpy(), observed.to_numpy(), atol=5e-7)
    np.testing.assert_allclose(oof.expected.to_numpy(), expected.to_numpy(), atol=5e-7)
    np.testing.assert_array_equal(
        oof.residuals.to_numpy(), oof.observed.to_numpy() - oof.expected.to_numpy()
    )
    assert sorted(set(oof.fold)) == [1, 2]
    assert oof.forecaster.model_dump() == FORECASTER
    text = gzip.decompress((directory / "residuals_long.csv.gz").read_bytes())
    assert oof.residuals_sha256 == hashlib.sha256(text).hexdigest()
    folds = (directory / "folds.json").read_bytes()
    assert oof.folds_sha256 == hashlib.sha256(folds).hexdigest()


def test_a_hash_that_differs_from_the_declaration_is_refused(work: Path) -> None:
    def tamper(document: dict[str, object]) -> dict[str, object]:
        files = document["files"]
        assert isinstance(files, dict)
        files["residuals_long.csv.gz"]["sha256_of_csv_text"] = "0" * 64
        return document

    with pytest.raises(ValueError, match="says"):
        load_oof_residuals(write(work, declaration=tamper), registry())


def test_a_declaration_of_another_year_is_refused(work: Path) -> None:
    def year(document: dict[str, object]) -> dict[str, object]:
        return document | {"year": 2017}

    with pytest.raises(ValueError, match="year"):
        load_oof_residuals(write(work, declaration=year), registry())


def table() -> pd.DataFrame:
    return long_table(*frames())


def test_a_sensor_missing_in_one_window_is_refused(work: Path) -> None:
    with pytest.raises(ValueError, match="sensor is missing"):
        load_oof_residuals(write(work, table().drop(index=5)), registry())


def test_a_sensor_missing_in_as_many_windows_as_sensors_is_still_refused(work: Path) -> None:
    rows = table().drop(index=[5, 9, 13, 17])  # P2 of four windows: the count stays a multiple

    with pytest.raises(ValueError, match="registry order"):
        load_oof_residuals(write(work, rows), registry())


def test_sensors_out_of_registry_order_are_refused(work: Path) -> None:
    rows = table()
    order = [0, 1, 2, 3, 5, 4, *range(6, len(rows))]  # P1 and P2 swapped in window 1
    rows = rows.iloc[order].reset_index(drop=True)

    with pytest.raises(ValueError, match="registry order"):
        load_oof_residuals(write(work, rows), registry())


def test_a_sensor_outside_the_registry_is_refused(work: Path) -> None:
    with pytest.raises(ValueError, match="registry order"):
        load_oof_residuals(write(work), registry(("P1", "P2", "P3", "P9")))


def test_a_missing_window_is_refused(work: Path) -> None:
    rows = table().drop(index=range(40, 44)).reset_index(drop=True)  # all of window 10

    with pytest.raises(ValueError, match="regular 5-minute grid"):
        load_oof_residuals(write(work, rows), registry())


def test_a_window_outside_2018_is_refused(work: Path) -> None:
    observed, expected = wide_frames(
        start="2017-12-31 23:00", windows=WINDOWS, seed=1, noise_m=0.02
    )

    with pytest.raises(ValueError, match="2018"):
        load_oof_residuals(write(work, long_table(observed, expected)), registry())


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_a_non_finite_value_is_refused(work: Path, value: float) -> None:
    rows = table()
    rows.loc[7, "observed"] = value

    with pytest.raises(ValueError, match="non-finite observed"):
        load_oof_residuals(write(work, rows), registry())


def test_a_window_without_a_fold_is_refused(work: Path) -> None:
    rows = table().astype({"fold": "float64"})
    rows.loc[8:11, "fold"] = math.nan

    with pytest.raises(ValueError, match="no fold"):
        load_oof_residuals(write(work, rows), registry())


def test_a_fold_outside_its_span_in_folds_json_is_refused(work: Path) -> None:
    rows = table()
    rows.loc[0:3, "fold"] = 2  # a January window labelled February

    with pytest.raises(ValueError, match="outside its span"):
        load_oof_residuals(write(work, rows), registry())


def test_different_folds_inside_one_window_are_refused(work: Path) -> None:
    rows = table()
    rows.loc[1, "fold"] = 2

    with pytest.raises(ValueError, match="different folds"):
        load_oof_residuals(write(work, rows), registry())


def test_a_path_that_names_2019_is_refused_before_anything_is_read(work: Path) -> None:
    directory = work / "oof-2019"  # never created: the refusal comes first

    with pytest.raises(ValueError, match="2019"):
        load_oof_residuals(directory, registry())
    with pytest.raises(ValueError, match="ADR-008"):
        refuse_2019(work / "eval" / "leaks_2019.csv")


def test_a_symlink_cannot_hide_a_2019_target(work: Path) -> None:
    target = work / "data-2019"
    target.mkdir()
    link = work / "innocent"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="2019"):
        refuse_2019(link)


def test_z_is_minus_the_residual_over_sigma_in_the_detector_arithmetic() -> None:
    residuals = pd.DataFrame({"A": [0.3, -0.1], "B": [0.0, 0.25]})

    z = z_matrix(residuals, {"A": 0.1, "B": 0.05})

    assert z.dtype == np.float64
    assert z.tolist() == [[-0.3 / 0.1, -0.0 / 0.05], [0.1 / 0.1, -0.25 / 0.05]]
    with pytest.raises(ValueError, match="order"):
        z_matrix(residuals, {"B": 0.05, "A": 0.1})


def test_the_quantization_step_is_the_measurement_resolution() -> None:
    observed = pd.DataFrame({"A": [40.12, 40.13, 40.15, 40.12], "B": [3.5, 3.55, 3.6, 3.45]})

    step = quantization_step(observed, decimals=6)

    assert step.to_dict() == {"A": 0.01, "B": 0.05}
    with pytest.raises(ValueError, match="fewer than two distinct"):
        quantization_step(pd.DataFrame({"C": [1.0, 1.0]}), decimals=6)


def test_the_lag1_autocorrelation_recovers_an_ar1_coefficient() -> None:
    series = ar1(random.Random(7), 50_000, 0.8)

    phi = lag1_autocorrelation(pd.DataFrame({"A": series}))

    assert phi["A"] == pytest.approx(0.8, abs=0.01)
    with pytest.raises(ValueError, match="constant"):
        lag1_autocorrelation(pd.DataFrame({"C": [2.0, 2.0, 2.0]}))


def test_the_inflation_factor() -> None:
    assert inflation_factor(0.0) == 1.0
    assert inflation_factor(0.9) == pytest.approx(19.0)
    assert inflation_factor(1.0) == math.inf
