"""XGBoost/Snap ML and GRU forecasters. These need the VM image libraries (numpy, xgboost,
snapml, tensorflow); they are skipped where the libraries are absent (e.g. backend CI)."""

import json
from pathlib import Path
from typing import Any

import pytest

from sentinel.forecasting.features import canonical_id, gru_feature_names, xgb_feature_names
from sentinel.forecasting.gru import WEIGHTS_FILE, GRUForecaster, build_gru_model, export_onnx
from sentinel.forecasting.interface import Forecaster, ForecastError
from sentinel.forecasting.registry import load_forecaster
from sentinel.forecasting.xgb import XGBoostForecaster
from tests.forecasting_helpers import LAYOUT, T0, request, short_ids, window

np: Any = pytest.importorskip("numpy")


def _train_xgb(tmp_path: Path) -> Path:
    xgb: Any = pytest.importorskip("xgboost")
    rng = np.random.default_rng(0)
    model_dir = tmp_path / "xgboost-33@0.0.1"
    model_dir.mkdir()
    names_by_sensor = {}
    for sensor in LAYOUT.pressure:
        names = xgb_feature_names(LAYOUT, sensor)
        x = rng.normal(size=(400, len(names))).astype(np.float32)
        y = 50 + x[:, 0] * 2 + x[:, -1]
        model = xgb.XGBRegressor(n_estimators=20, max_depth=3)
        model.fit(x, y)
        model.get_booster().feature_names = names
        model.get_booster().save_model(str(model_dir / f"{sensor}.json"))
        names_by_sensor[sensor] = names
    meta = {
        "version": "0.0.1",
        "layout": LAYOUT.to_dict(),
        "sensors": list(LAYOUT.pressure),
        "feature_names": names_by_sensor,
    }
    (model_dir / "meta.json").write_text(json.dumps(meta))
    return model_dir


def test_xgboost_native_engine(tmp_path: Path) -> None:

    model = XGBoostForecaster(_train_xgb(tmp_path), engine="xgboost")
    assert isinstance(model, Forecaster)
    result = model.predict(request(T0))
    assert set(result.expected_pressures) == set(LAYOUT.pressure)
    assert result.contributions is not None
    for sensor, groups in result.contributions.items():
        # TreeSHAP additivity: base + groups == forecast
        assert sum(groups.values()) == pytest.approx(result.expected_pressures[sensor], abs=1e-3)


def test_load_forecaster_passes_the_xgboost_options(tmp_path: Path) -> None:
    model_dir = _train_xgb(tmp_path)
    without = load_forecaster("xgboost", model_dir, engine="xgboost", with_contributions=False)
    with_shap = load_forecaster("xgboost", model_dir, engine="xgboost")
    assert without.predict(request(T0)).contributions is None
    assert with_shap.predict(request(T0)).contributions is not None
    assert without.predict(request(T0)).expected_pressures == pytest.approx(
        with_shap.predict(request(T0)).expected_pressures
    )


def test_on_alert_explains_only_the_requested_sensors(tmp_path: Path) -> None:
    model_dir = _train_xgb(tmp_path)
    every = XGBoostForecaster(model_dir, engine="xgboost", with_contributions="all")
    lazy = XGBoostForecaster(model_dir, engine="xgboost", with_contributions="on_alert")
    off = XGBoostForecaster(model_dir, engine="xgboost", with_contributions="off")
    req = request(T0)
    assert lazy.predict(req).contributions is None
    full = every.predict(req)
    assert full.contributions is not None
    some = list(full.expected_pressures)[:2]
    explained = lazy.explain(req, some)
    assert explained is not None and set(explained) == set(some)
    for sensor in some:
        assert explained[sensor] == pytest.approx(full.contributions[sensor])
        assert sum(explained[sensor].values()) == pytest.approx(
            full.expected_pressures[sensor], abs=1e-3
        )
    assert off.explain(req, some) is None
    with pytest.raises(ForecastError):
        lazy.explain(req, ["not-a-sensor"])
    with pytest.raises(ValueError):
        XGBoostForecaster(model_dir, engine="xgboost", with_contributions="sometimes")


def test_snapml_engine_matches_xgboost(tmp_path: Path) -> None:
    pytest.importorskip("snapml")

    model_dir = _train_xgb(tmp_path)
    native = XGBoostForecaster(model_dir, engine="xgboost", with_contributions=False)
    snap = XGBoostForecaster(model_dir, engine="snapml", with_contributions=False)
    a = native.predict(request(T0)).expected_pressures
    b = snap.predict(request(T0)).expected_pressures
    for sensor in a:
        assert b[sensor] == pytest.approx(a[sensor], abs=1e-3)


def test_xgboost_rejects_feature_drift(tmp_path: Path) -> None:

    model_dir = _train_xgb(tmp_path)
    meta = json.loads((model_dir / "meta.json").read_text())
    meta["feature_names"][LAYOUT.pressure[0]] = ["cal:hour"]
    (model_dir / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        XGBoostForecaster(model_dir, engine="xgboost")


def _train_gru(tmp_path: Path, window_len: int = 6, horizon: int = 1) -> Path:
    pytest.importorskip("tensorflow")

    names = gru_feature_names(LAYOUT)
    model = build_gru_model(window_len, len(names), len(LAYOUT.pressure), units=8)
    model_dir = tmp_path / "gru-33@0.0.1"
    model_dir.mkdir()
    model.save_weights(str(model_dir / WEIGHTS_FILE))
    meta = {
        "version": "0.0.1",
        "layout": LAYOUT.to_dict(),
        "window": window_len,
        "horizon": horizon,
        "units": 8,
        "feature_names": names,
        "x_mean": [0.0] * len(names),
        "x_std": [1.0] * len(names),
        "y_mean": [50.0] * len(LAYOUT.pressure),
        "y_std": [2.0] * len(LAYOUT.pressure),
    }
    (model_dir / "meta.json").write_text(json.dumps(meta))
    return model_dir


def test_gru_predicts_from_history_only(tmp_path: Path) -> None:

    model = GRUForecaster(_train_gru(tmp_path))
    assert isinstance(model, Forecaster)
    assert model.min_history_windows == 6
    req = request(T0, history=8)
    result = model.predict(req)
    assert set(result.expected_pressures) == set(LAYOUT.pressure)
    # changing the window under test must not change the forecast (no leakage)
    other = req.model_copy(update={"current": window(T0, shift=10.0)})
    assert model.predict(other).expected_pressures == result.expected_pressures


def test_gru_needs_enough_history(tmp_path: Path) -> None:

    model = GRUForecaster(_train_gru(tmp_path))
    with pytest.raises(ForecastError):
        model.predict(request(T0, history=3))


def test_gru_horizon_ignores_the_most_recent_windows(tmp_path: Path) -> None:
    model = GRUForecaster(_train_gru(tmp_path, window_len=6, horizon=3))
    assert model.min_history_windows == 8
    req = request(T0, history=10)
    base = model.predict(req).expected_pressures
    # the last horizon-1 = 2 history windows are not inputs: changing them changes nothing
    changed = (*req.history[:-2], *(window(w.timestamp, shift=5.0) for w in req.history[-2:]))
    other = req.model_copy(update={"history": changed})
    assert model.predict(other).expected_pressures == base


def test_export_onnx_has_one_fused_gru_node(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    onnx: Any = pytest.importorskip("onnx")
    model = GRUForecaster(_train_gru(tmp_path))
    path = tmp_path / "model.onnx"
    info = export_onnx(model.keras_model, path)
    assert info["gru_nodes"] == 1
    assert info["linear_before_reset"] == 1
    assert info["max_abs_diff_torch_vs_keras"] < 1e-5
    reference = pytest.importorskip("onnx.reference")
    x = (
        np.random.default_rng(1)
        .normal(size=(2, 6, len(gru_feature_names(LAYOUT))))
        .astype(np.float32)
    )
    (onnx_out,) = reference.ReferenceEvaluator(onnx.load(str(path))).run(None, {"window": x})
    keras_out = model.keras_model(x, training=False).numpy()
    assert np.abs(onnx_out - keras_out).max() < 1e-4


def test_xgboost_and_gru_accept_both_id_spellings(tmp_path: Path) -> None:
    xgb_model = XGBoostForecaster(_train_xgb(tmp_path), engine="xgboost")
    long_req = request(T0, history=8)
    a = xgb_model.predict(long_req)
    b = xgb_model.predict(short_ids(long_req))
    assert b.expected_pressures == {canonical_id(k): v for k, v in a.expected_pressures.items()}
    assert b.contributions is not None and set(b.contributions) == set(b.expected_pressures)

    gru = GRUForecaster(_train_gru(tmp_path))
    c = gru.predict(long_req).expected_pressures
    d = gru.predict(short_ids(long_req)).expected_pressures
    assert d == {canonical_id(k): v for k, v in c.items()}
