"""Out-of-fold residuals for the detector calibration (DEP-DET-05). Pandas parts run in CI; the
model parts need xgboost and are skipped without it."""

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from sentinel.forecasting import oof, training
from sentinel.forecasting.features import SensorLayout

PRESSURE = ("n1", "n4", "n31")
FLOW = ("p227", "p235", "PUMP_1")
LEVEL = ("T1",)
LAYOUT = SensorLayout(pressure=PRESSURE, flow=FLOW, tank_level=LEVEL)


def _quiet(message: str) -> None:
    del message


def _year(freq: str = "2h") -> pd.DataFrame:
    """One synthetic 2018 in the layout IDs: pressures follow the flows plus noise."""
    idx = pd.date_range("2018-01-01 00:00", "2018-12-31 23:55", freq=freq, name="timestamp")
    hour = (idx.hour + idx.minute / 60).to_numpy()
    rng = np.random.default_rng(0)
    cols: dict[str, Any] = {}
    for i, p in enumerate(FLOW):
        cols[p] = 20 + i + np.cos(2 * np.pi * hour / 24) + 0.1 * rng.normal(size=len(idx))
    cols["T1"] = 3 + 0.1 * np.sin(2 * np.pi * hour / 24)
    for i, n in enumerate(PRESSURE):
        cols[n] = 50 + i - 0.5 * cols["p227"] + 0.01 * rng.normal(size=len(idx))
    return pd.DataFrame(cols, index=idx)[[*PRESSURE, *FLOW, *LEVEL]]


def test_variants_drop_only_the_neighbouring_pressures() -> None:
    full = oof.variant_features(LAYOUT, "n1", "with-neighbours")
    bare = oof.variant_features(LAYOUT, "n1", "without-neighbours")
    assert "pressure:n4" in full and "pressure:n1" not in full
    assert bare == [n for n in full if not n.startswith("pressure:")]
    assert oof.declaration(LAYOUT, "without-neighbours")["other_pressures_same_window"] is False
    with pytest.raises(ValueError):
        oof.variant_features(LAYOUT, "n1", "with-lags")


def test_folds_are_the_calendar_months() -> None:
    df = _year()
    folds = oof.month_folds(pd.DatetimeIndex(df.index))
    assert sorted(set(folds)) == list(range(1, 13))
    assert folds.loc["2018-03-31 22:00"] == 3 and folds.loc["2018-04-01 00:00"] == 4
    doc = oof.folds_document(folds)
    assert doc["folds"]["02"] == ["2018-02-01T00:00:00", "2018-02-28T22:00:00"]
    two_years = pd.date_range("2018-12-31 22:00", periods=3, freq="2h")
    with pytest.raises(ValueError):
        oof.month_folds(two_years)


def test_long_table_layout_and_reproducible_text_hash(tmp_path: Path) -> None:
    df = _year().iloc[:4]
    expected = df[list(PRESSURE)] - 0.25
    folds = oof.month_folds(pd.DatetimeIndex(df.index))
    df.iloc[1, 0] = np.nan  # a missing observation leaves only that row out
    table = oof.long_table(df, expected, folds)
    assert list(table.columns) == ["timestamp", "sensor", "observed", "expected", "fold"]
    assert len(table) == 4 * len(PRESSURE) - 1
    assert list(table["sensor"][:3]) == list(PRESSURE)
    np.testing.assert_allclose(table["observed"] - table["expected"], 0.25)

    path = tmp_path / oof.LONG_FILE
    digest = oof.write_long(table, path)
    text = gzip.decompress(path.read_bytes())
    assert hashlib.sha256(text).hexdigest() == digest
    assert text.decode().splitlines()[0] == "timestamp,sensor,observed,expected,fold"
    assert text.decode().splitlines()[1].startswith("2018-01-01 00:00:00,n1,")
    assert oof.write_long(table, tmp_path / "again.csv.gz") == digest
    assert (tmp_path / "again.csv.gz").read_bytes() == path.read_bytes()


def test_a_month_is_predicted_by_models_that_never_saw_it(tmp_path: Path) -> None:
    pytest.importorskip("xgboost")
    df = _year()
    clean = pd.Series(True, index=df.index)
    params = {"learning_rate": 0.3, "max_depth": 3, "tree_method": "hist"}
    trees = dict.fromkeys(PRESSURE, 10)
    sensors = ["n1"]
    base, folds = oof.oof_expected(
        df, LAYOUT, clean, "with-neighbours", params, trees, sensors=sensors, progress=_quiet
    )
    assert base.index.equals(df.index) and base.notna().all().all()

    # Shift n1 by 5 m in June only: June's own forecasts do not move (its models never saw
    # June), every other month's do (June is in their training data).
    shifted = df.copy()
    june = folds.to_numpy() == 6
    shifted.loc[june, "n1"] += 5.0
    moved, _ = oof.oof_expected(
        shifted, LAYOUT, clean, "with-neighbours", params, trees, sensors=sensors, progress=_quiet
    )
    np.testing.assert_array_equal(moved[june].to_numpy(), base[june].to_numpy())
    assert not np.allclose(moved[~june].to_numpy(), base[~june].to_numpy())


def test_dirty_windows_are_predicted_but_never_trained_on() -> None:
    pytest.importorskip("xgboost")
    df = _year()
    clean = pd.Series(True, index=df.index)
    leak = (df.index >= "2018-03-10") & (df.index < "2018-03-20")
    clean[leak] = False
    params = {"learning_rate": 0.3, "max_depth": 3, "tree_method": "hist"}
    trees = dict.fromkeys(PRESSURE, 10)
    base, _ = oof.oof_expected(df, LAYOUT, clean, "with-neighbours", params, trees, progress=_quiet)
    dropped = df.copy()
    dropped.loc[leak, "n1"] -= 3.0  # a "leak" inside the masked windows
    after, _ = oof.oof_expected(
        dropped, LAYOUT, clean, "with-neighbours", params, trees, progress=_quiet
    )
    # the masked windows never reach a training set, so no other month changes...
    np.testing.assert_array_equal(after[~leak]["n1"].to_numpy(), base[~leak]["n1"].to_numpy())
    # ...and they are still predicted
    assert after[leak].notna().all().all()


def test_cache_resumes_finished_folds(tmp_path: Path) -> None:
    pytest.importorskip("xgboost")
    df = _year()
    clean = pd.Series(True, index=df.index)
    params = {"max_depth": 2, "tree_method": "hist"}
    trees = dict.fromkeys(PRESSURE, 5)
    messages: list[str] = []
    first, _ = oof.oof_expected(
        df, LAYOUT, clean, "without-neighbours", params, trees,
        cache_dir=tmp_path, progress=messages.append,
    )  # fmt: skip
    assert len(list(tmp_path.glob("fold-*.csv.gz"))) == 12
    messages.clear()
    again, _ = oof.oof_expected(
        df, LAYOUT, clean, "without-neighbours", params, trees,
        cache_dir=tmp_path, progress=messages.append,
    )  # fmt: skip
    assert all("reused" in m for m in messages) and len(messages) == 12
    np.testing.assert_array_equal(again.to_numpy(), first.to_numpy())


def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v1 = tmp_path / "data" / "v1"
    v1.mkdir(parents=True)
    df = _year("4h")
    prefix = {**dict.fromkeys(PRESSURE, "Pressure_"), **dict.fromkeys(FLOW, "Flow_")}
    raw = df.rename(columns={c: prefix.get(c, "Level_") + c for c in df.columns})
    raw.to_csv(v1 / "scada_2018.csv.gz", compression="gzip")
    pd.DataFrame(
        [
            {
                "pipe": "p1",
                "start": "2018-05-02 00:00",
                "end": "2018-05-09 00:00",
                "leak_size": 0.01,
                "type": "abrupt",
                "peak": "2018-05-02 00:00",
                "repaired_in_2018": True,
            },
            {
                "pipe": "p2",
                "start": "2018-08-01 00:00",
                "end": "2018-12-31 23:55",
                "leak_size": 0.01,
                "type": "incipient",
                "peak": "2018-09-01 00:00",
                "repaired_in_2018": False,
            },
        ]
    ).to_csv(v1 / "leaks_2018.csv", index=False)
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    model = tmp_path / "models" / "xgboost-33@0.0.1"
    model.mkdir(parents=True)
    training.write_json(
        model / "meta.json",
        training.artefact_meta(
            "xgboost-33",
            "0.0.1",
            LAYOUT,
            sensors=list(PRESSURE),
            params={"n_estimators": 50, "max_depth": 3, "early_stopping_rounds": 5},
            best_iteration=dict.fromkeys(PRESSURE, 8),
            mask_policy="reparados",
        ),
    )
    return model


def test_run_writes_both_variants_with_their_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("xgboost")
    model = _data_dir(tmp_path, monkeypatch)
    out = oof.run(model, tmp_path / "oof", progress=_quiet)
    assert set(out) == set(oof.VARIANTS)
    assert out["with-neighbours"] == tmp_path / "oof" / "xgboost-33@0.0.1" / "with-neighbours"
    for variant, folder in out.items():
        doc = json.loads((folder / oof.DECLARATION_FILE).read_text())
        assert doc["variant"] == variant and doc["year"] == 2018
        assert doc["training"]["mask_policy"] == "reparados"
        table = pd.read_csv(folder / oof.LONG_FILE)
        assert len(table) == doc["files"][oof.LONG_FILE]["rows"] == 2190 * len(PRESSURE)
        text = gzip.decompress((folder / oof.LONG_FILE).read_bytes())
        assert hashlib.sha256(text).hexdigest() == doc["files"][oof.LONG_FILE]["sha256_of_csv_text"]
        assert set(doc["summary_by_mask"]) == set(oof.SUMMARY_POLICIES)
    with_n = json.loads((out["with-neighbours"] / oof.DECLARATION_FILE).read_text())
    assert with_n["forecaster"] == {"name": "xgboost-33", "version": "0.0.1"}
    assert with_n["training"]["trees_per_sensor"] == dict.fromkeys(PRESSURE, 8)
    assert with_n["delivered"] is True
    without = json.loads((out["without-neighbours"] / oof.DECLARATION_FILE).read_text())
    assert without["delivered"] is False
    assert without["inputs"]["other_pressures_same_window"] is False
