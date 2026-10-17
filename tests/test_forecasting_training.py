"""Training helpers (pandas). Run in CI: pandas is in the dev group."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sentinel.data.clean_mask import mascara_limpa
from sentinel.forecasting import training
from sentinel.forecasting.baseline import HourOfWeekForecaster

PRESSURE = ("n1", "n4", "n31")
FLOW = ("p227", "p235", "PUMP_1")
LEVEL = ("T1",)


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Three days of 2018 in the data owner's v1 format, plus both registry spellings."""
    v1 = tmp_path / "data" / "v1"
    v1.mkdir(parents=True)
    idx = pd.date_range("2018-01-01 00:00", periods=3 * 288, freq="5min", name="timestamp")
    hour = (idx.hour + idx.minute / 60).to_numpy()
    rng = np.random.default_rng(0)
    cols = {}
    for i, n in enumerate(PRESSURE):
        cols[f"Pressure_{n}"] = (
            50 + i + np.sin(2 * np.pi * hour / 24) + 0.01 * rng.normal(size=len(idx))
        )
    for i, p in enumerate(FLOW):
        cols[f"Flow_{p}"] = 20 + i + np.cos(2 * np.pi * hour / 24)
    cols["Level_T1"] = 3 + 0.1 * np.sin(2 * np.pi * hour / 24)
    pd.DataFrame(cols, index=idx).to_csv(v1 / "scada_2018.csv.gz", compression="gzip")
    rows = [
        {
            "sensor_id": f"Pressure_{n}",
            "tipo": "pressao",
            "unidade": "m",
            "elemento_rede": n,
            "papel": "",
        }
        for n in PRESSURE
    ]
    rows += [
        {
            "sensor_id": f"Flow_{p}",
            "tipo": "vazao",
            "unidade": "m3/h",
            "elemento_rede": p,
            "papel": "",
        }
        for p in FLOW
    ]
    rows += [
        {
            "sensor_id": "Level_T1",
            "tipo": "nivel",
            "unidade": "m",
            "elemento_rede": "T1",
            "papel": "",
        }
    ]
    pd.DataFrame(rows).to_csv(v1 / "sensors.csv", index=False)
    pd.DataFrame(
        [
            {
                "pipe": "p1",
                "start": "2018-01-02 00:00",
                "end": "2018-01-02 06:00",
                "leak_size": 0.01,
                "type": "abrupt",
                "peak": "2018-01-02 00:00",
                "repaired_in_2018": True,
            },
            {
                "pipe": "p2",
                "start": "2018-01-01 12:00",
                "end": "2018-12-31 23:55",
                "leak_size": 0.01,
                "type": "incipient",
                "peak": "2018-01-03 00:00",
                "repaired_in_2018": False,
            },
        ]
    ).to_csv(v1 / "leaks_2018.csv", index=False)
    registry = tmp_path / "sensors.battledim.json"
    registry.write_text(
        json.dumps(
            {
                "provisional": False,
                "pressure": list(PRESSURE),
                "flow": list(FLOW),
                "tank_level": list(LEVEL),
            }
        )
    )
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SENTINEL_SENSOR_REGISTRY", str(registry))
    return tmp_path


def test_layout_and_columns_follow_the_registry(data: Path) -> None:
    short = training.load_layout()
    assert short.pressure == PRESSURE
    df = training.load_scada(2018, short)
    assert list(df.columns) == [*PRESSURE, *FLOW, *LEVEL]

    long = training.load_layout(data / "data" / "v1" / "sensors.csv")
    assert long.pressure == tuple(f"Pressure_{n}" for n in PRESSURE)
    df_long = training.load_scada(2018, long)
    np.testing.assert_array_equal(df_long.to_numpy(), df.to_numpy())


def test_provisional_registry_is_rejected(tmp_path: Path) -> None:
    reg = tmp_path / "prov.json"
    reg.write_text(json.dumps({"provisional": True, "pressure": ["P01"]}))
    with pytest.raises(ValueError):
        training.load_layout(reg)


def test_2019_is_never_loaded(data: Path) -> None:
    with pytest.raises(ValueError):
        training.load_scada(2019, training.load_layout())


def test_clean_mask_is_the_data_owners_mask(data: Path) -> None:
    df = training.load_scada(2018, training.load_layout())
    for policy in ("reparados", "todos"):
        ours = training.clean_mask(pd.DatetimeIndex(df.index), policy)
        theirs = mascara_limpa(pd.DatetimeIndex(df.index), training.load_leaks_2018(), policy)
        pd.testing.assert_series_equal(ours, theirs)
    default = training.clean_mask(pd.DatetimeIndex(df.index))
    assert not default[pd.Timestamp("2018-01-02 03:00")]  # repaired leak excluded
    assert default[pd.Timestamp("2018-01-01 18:00")]  # unrepaired leak kept ("normal" state)


def test_feature_parity_with_runtime(data: Path) -> None:
    layout = training.load_layout()
    df = training.load_scada(2018, layout)
    assert training.assert_feature_parity(df, layout, n=20) == 20


def test_sequence_weights_need_a_fully_clean_stretch() -> None:
    clean = pd.Series([True, True, True, False, True, True, True, True])
    w = training.sequence_weights(clean, lag=2)
    np.testing.assert_array_equal(w, [0, 0, 1, 0, 0, 0, 1, 1])


def test_baseline_fit_matches_runtime_forecaster(data: Path) -> None:
    layout = training.load_layout()
    df = training.load_scada(2018, layout)
    prof = training.fit_hour_of_week(df, layout)
    model = HourOfWeekForecaster({s: prof[s].tolist() for s in layout.pressure})
    expected = training.predict_hour_of_week(prof, df)
    for ts in pd.DatetimeIndex(df.index)[[5, 300, 700]]:
        out = model.predict(training.build_request(df, ts, layout, history=1)).expected_pressures
        for s in layout.pressure:
            assert out[s] == pytest.approx(expected.loc[ts, s])


def test_metrics() -> None:
    resid = pd.DataFrame({"a": [0.0, 0.1, -0.1, 3.0], "b": [0.0, 0.2, -0.2, 0.0]})
    sigma = training.robust_sigma(resid)
    assert sigma["a"] < 0.3  # the outlier does not inflate sigma
    clean = pd.Series([True, True, True, False])
    assert training.leak_contrast(resid, sigma, pd.Series([True] * 4)) is None
    assert training.low_variance_sensors(pd.Series({"x": 0.005, "y": 0.5})) == {"x": 0.005}
    # MAE on clean windows ignores the leak window
    assert training.mae_by_sensor(resid, clean)["a"] == pytest.approx(0.2 / 3)
    assert training.mae_by_sensor(resid)["a"] == pytest.approx(3.2 / 4)


def test_leak_contrast_counts_only_pressure_drops() -> None:
    sigma = pd.Series({"a": 0.1, "b": 0.1})
    clean = pd.Series([True, True, True, True, False, False])
    normal = [0.1, -0.1, 0.1, -0.1]  # residual = observed - expected
    drop = pd.DataFrame({"a": [*normal, -0.5, -0.5], "b": [*normal, -0.3, -0.3]})
    rise = drop.copy()
    rise.iloc[4:] = -drop.iloc[4:]  # same size, wrong direction during the leak

    to_drop = training.leak_contrast(drop, sigma, clean)
    to_rise = training.leak_contrast(rise, sigma, clean)
    assert to_drop is not None and to_drop > 1  # leak shows as a pressure drop
    assert to_rise == 0.0  # pressure above expected during the leak is no evidence
    # the old |z| version would have scored both the same
    assert (drop.abs() == rise.abs()).all().all()
