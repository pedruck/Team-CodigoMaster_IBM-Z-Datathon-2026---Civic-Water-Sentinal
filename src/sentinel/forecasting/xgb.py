"""XGBoost forecaster served with Snap ML (step 2 of the model ladder, design doc §3-§5).

One gradient-boosted tree model per pressure sensor, trained off-platform with XGBoost and
exported as XGBoost JSON (portable across x86 and s390x, design doc §4). On LinuxONE the
JSON is imported into Snap ML; with ``tree_format="auto"`` Snap ML uses the Telum AI
accelerator (zDNN) when it is available and compressed trees on the CPU otherwise.

TreeSHAP contributions (``pred_contribs``) come from the native XGBoost booster, because
Snap ML does not compute them. They are grouped as in the alert payload (design doc §6).

numpy, snapml and xgboost are imported lazily: they exist on the VM image but are not
backend dependencies, so the rest of the package (and CI) does not need them.
"""

import importlib
import json
import tempfile
from pathlib import Path
from typing import Any

from sentinel.forecasting.features import (
    SensorLayout,
    group_contributions,
    xgb_feature_names,
    xgb_features,
)
from sentinel.forecasting.interface import ForecastError
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult

MODEL_NAME = "xgboost-33"
ENGINES = ("snapml", "xgboost")


def _lazy(module: str) -> Any:
    """Imports an optional ML library only when a model is actually loaded."""
    return importlib.import_module(module)


def _snapml_regressor() -> Any:
    return _lazy("snapml").BoostingMachineRegressor()


def _xgboost() -> Any:
    return _lazy("xgboost")


def _numpy() -> Any:
    return _lazy("numpy")


def portable_booster_json(text: str) -> str:
    """Rewrites an XGBoost JSON model so that Snap ML can import it.

    XGBoost 3.x stores ``learner_model_param.base_score`` as a vector string (``"[8.2E-1]"``).
    Snap ML 1.17 (the version on the organisers' LinuxONE image) cannot parse it and fails
    with "no objective defined". For single-target regression the vector has one element, so
    writing it as a scalar is lossless; XGBoost itself still loads the result unchanged.
    """
    model = json.loads(text)
    params = model["learner"]["learner_model_param"]
    base = str(params["base_score"]).strip()
    if base.startswith("["):
        values = [v for v in base.strip("[]").split(",") if v.strip()]
        if len(values) != 1:
            raise ValueError(f"multi-target base_score is not supported: {base}")
        params["base_score"] = values[0].strip()
    return json.dumps(model, separators=(",", ":"))


class XGBoostForecaster:
    """Loads ``<model_dir>/meta.json`` and one ``<sensor>.json`` booster per pressure sensor."""

    def __init__(
        self,
        model_dir: str | Path,
        *,
        engine: str = "snapml",
        tree_format: str = "auto",
        with_contributions: bool = True,
    ) -> None:
        if engine not in ENGINES:
            raise ValueError(f"engine must be one of {ENGINES}, got {engine!r}")
        self._dir = Path(model_dir)
        meta = json.loads((self._dir / "meta.json").read_text(encoding="utf-8"))
        self._layout = SensorLayout.from_dict(meta["layout"])
        self._sensors: tuple[str, ...] = tuple(meta["sensors"])
        self._names = {s: xgb_feature_names(self._layout, s) for s in self._sensors}
        stored = meta.get("feature_names", {})
        drift = [s for s in self._sensors if s in stored and stored[s] != self._names[s]]
        if drift:
            raise ValueError(f"feature definition changed since training for: {drift}")

        self._np = _numpy()
        self._engine = engine
        self._with_contributions = with_contributions
        self._models: dict[str, Any] = {}
        self._boosters: dict[str, Any] = {}
        self.tree_format_used: str = "xgboost-native"

        xgb = _xgboost() if (engine == "xgboost" or with_contributions) else None
        for sensor in self._sensors:
            path = self._dir / f"{sensor}.json"
            if xgb is not None:
                booster = xgb.Booster()
                booster.load_model(str(path))
                self._boosters[sensor] = booster
            if engine == "snapml":
                self._models[sensor] = self._import_snapml(path, tree_format)
        if engine == "snapml" and self._models:
            first = next(iter(self._models.values()))
            booster_ = getattr(first, "booster_", None)
            self.tree_format_used = str(getattr(booster_, "optimized_tree_format_", tree_format))

        self._info = ModelInfo(name=MODEL_NAME, version=str(meta["version"]), is_mock=False)

    @property
    def info(self) -> ModelInfo:
        return self._info

    @property
    def min_history_windows(self) -> int:
        return 1  # uses the window being scored (other sensors, flows, level, calendar)

    @property
    def layout(self) -> SensorLayout:
        return self._layout

    def predict(self, request: ForecastRequest) -> ForecastResult:
        missing = [s for s in request.pressure_sensor_ids if s not in self._names]
        if missing:
            raise ForecastError(f"sensors without a model: {missing}")
        np = self._np
        expected: dict[str, float] = {}
        contributions: dict[str, dict[str, float]] = {}
        try:
            for sensor in request.pressure_sensor_ids:
                row = xgb_features(request.current, self._layout, sensor)
                x = np.asarray([row], dtype=np.float32)
                if self._engine == "snapml":
                    expected[sensor] = float(self._models[sensor].predict(x)[0])
                else:
                    expected[sensor] = float(self._booster_predict(sensor, x, False)[0])
                if self._with_contributions:
                    values = [float(v) for v in self._booster_predict(sensor, x, True)[0]]
                    contributions[sensor] = group_contributions(
                        self._names[sensor], values[:-1], values[-1]
                    )
        except KeyError as exc:
            raise ForecastError(f"window is missing an input sensor: {exc}") from exc
        return ForecastResult(
            timestamp=request.target_timestamp,
            expected_pressures=expected,
            model=self._info,
            contributions=contributions or None,
        )

    @staticmethod
    def _import_snapml(path: Path, tree_format: str) -> Any:
        text = path.read_text(encoding="utf-8")
        portable = portable_booster_json(text)
        model = _snapml_regressor()
        if portable == text:
            model.import_model(str(path), "xgb_json", tree_format=tree_format)
            return model
        with tempfile.TemporaryDirectory() as tmp:
            fixed = Path(tmp) / path.name
            fixed.write_text(portable, encoding="utf-8")
            model.import_model(str(fixed), "xgb_json", tree_format=tree_format)
        return model

    def _booster_predict(self, sensor: str, x: Any, contribs: bool) -> Any:
        xgb = _xgboost()
        matrix = xgb.DMatrix(x, feature_names=self._names[sensor])
        return self._boosters[sensor].predict(matrix, pred_contribs=contribs)
