"""GRU forecaster (step 3 of the model ladder, design doc §3-§5).

A small GRU reads ``window`` 5-minute steps (12 h by default) of every sensor and predicts all
pressure sensors ``horizon`` steps after the last step it read (``horizon=1``: the next
reading, as in the design doc). It only reads ``request.history``, never the window being
scored, so it cannot see the observation under test (contracts §2).

The layer settings follow the IBM Z accelerator constraints quoted in the design doc §4:
sigmoid/tanh activations, no clipping and ``reset_after=True`` (ONNX
``linear_before_reset = 1``). On the VM image TensorFlow runs with ``ibm-zdnn-plugin``, which
dispatches supported ops to the Telum NNPA, as in the organisers' examples.

For the zDLC path the model is exported to ONNX through PyTorch (``export_onnx``): Keras 3
models converted with tf2onnx become a generic ``Loop``, while ``torch.nn.GRU`` (same maths as
``reset_after=True``) exports as one ONNX ``GRU`` node with ``linear_before_reset=1``.

Weights move between platforms as Keras ``.weights.h5`` (HDF5 records byte order) and ONNX.
TensorFlow, numpy and torch are imported lazily (VM image only, not backend dependencies).
"""

import importlib
import json
from pathlib import Path
from typing import Any

from sentinel.forecasting.features import (
    IdTranslator,
    SensorLayout,
    gru_feature_names,
    gru_step_features,
)
from sentinel.forecasting.integrity import versioned
from sentinel.forecasting.interface import ForecastError
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult

MODEL_NAME = "gru-33"
WEIGHTS_FILE = "model.weights.h5"
ONNX_FILE = "model.onnx"


def _tensorflow() -> Any:
    return importlib.import_module("tensorflow")


def _numpy() -> Any:
    return importlib.import_module("numpy")


def build_gru_model(window: int, n_features: int, n_outputs: int, units: int = 64) -> Any:
    """Single source of the architecture, used by the training notebook and by inference."""
    tf = _tensorflow()
    return tf.keras.Sequential(
        [
            tf.keras.Input(shape=(window, n_features), name="window"),
            tf.keras.layers.GRU(
                units,
                activation="tanh",
                recurrent_activation="sigmoid",
                reset_after=True,
                name="gru",
            ),
            tf.keras.layers.Dense(n_outputs, name="pressures"),
        ],
        name=MODEL_NAME,
    )


def export_onnx(keras_model: Any, path: str | Path, opset: int = 17) -> dict[str, Any]:
    """Exports the Keras GRU to ONNX via an equivalent ``torch.nn.GRU`` and checks the result.

    Keras stores the gates as (update z, reset r, candidate h); PyTorch as (r, z, n). Both use
    the reset gate after the recurrent matmul (Keras ``reset_after=True`` = ONNX
    ``linear_before_reset=1``), so the conversion is a re-ordering, not an approximation.
    Returns the ONNX GRU attributes and the max |torch - keras| on random input.
    """
    torch = importlib.import_module("torch")
    onnx = importlib.import_module("onnx")
    np = _numpy()

    gru_layer = keras_model.get_layer("gru")
    dense = keras_model.get_layer("pressures")
    kernel, recurrent, bias = gru_layer.get_weights()
    units = recurrent.shape[0]
    window, n_features = keras_model.input_shape[1], keras_model.input_shape[2]

    def reorder(m: Any) -> Any:  # columns z|r|h -> rows r|z|n
        z, r, h = m[..., :units], m[..., units : 2 * units], m[..., 2 * units :]
        return np.concatenate([r, z, h], axis=-1)

    rnn = torch.nn.GRU(n_features, units, batch_first=True)
    head = torch.nn.Linear(units, dense.get_weights()[0].shape[1])
    with torch.no_grad():
        rnn.weight_ih_l0.copy_(torch.from_numpy(reorder(kernel).T.copy()))
        rnn.weight_hh_l0.copy_(torch.from_numpy(reorder(recurrent).T.copy()))
        rnn.bias_ih_l0.copy_(torch.from_numpy(reorder(bias[0]).copy()))
        rnn.bias_hh_l0.copy_(torch.from_numpy(reorder(bias[1]).copy()))
        head.weight.copy_(torch.from_numpy(dense.get_weights()[0].T.copy()))
        head.bias.copy_(torch.from_numpy(dense.get_weights()[1].copy()))

    def forward(self: Any, x: Any) -> Any:
        out, _ = self.rnn(x)
        return self.head(out[:, -1])

    def init(self: Any) -> None:
        torch.nn.Module.__init__(self)
        self.rnn, self.head = rnn, head

    net = type("GRUNet", (torch.nn.Module,), {"__init__": init, "forward": forward})().eval()

    sample = np.random.default_rng(0).normal(size=(4, window, n_features)).astype(np.float32)
    with torch.no_grad():
        torch_out = net(torch.from_numpy(sample)).numpy()
    keras_out = keras_model(sample, training=False).numpy()
    torch.onnx.export(
        net,
        (torch.from_numpy(sample[:1]),),
        str(path),
        input_names=["window"],
        output_names=["pressures"],
        dynamic_axes={"window": {0: "batch"}, "pressures": {0: "batch"}},
        opset_version=opset,
        dynamo=False,
    )
    model = onnx.load(str(path))
    onnx.checker.check_model(model)
    nodes = [n for n in model.graph.node if n.op_type == "GRU"]
    attrs = (
        {a.name: onnx.helper.get_attribute_value(a) for a in nodes[0].attribute} if nodes else {}
    )
    return {
        "opset": opset,
        "gru_nodes": len(nodes),
        "linear_before_reset": attrs.get("linear_before_reset"),
        "max_abs_diff_torch_vs_keras": float(np.abs(torch_out - keras_out).max()),
    }


class GRUForecaster:
    """Loads ``<model_dir>/meta.json`` (layout, window, horizon, scalers) and Keras weights."""

    def __init__(self, model_dir: str | Path, *, artefact_sha256: str | None = None) -> None:
        self._dir = Path(model_dir)
        meta = json.loads((self._dir / "meta.json").read_text(encoding="utf-8"))
        self._layout = SensorLayout.from_dict(meta["layout"])
        self._ids = IdTranslator(self._layout)
        self._window = int(meta["window"])
        self._horizon = int(meta.get("horizon", 1))
        if self._horizon < 1:
            raise ValueError("horizon must be >= 1")
        names = gru_feature_names(self._layout)
        if meta.get("feature_names", names) != names:
            raise ValueError("feature definition changed since training; retrain the GRU")

        np = _numpy()
        self._np = np
        self._x_mean = np.asarray(meta["x_mean"], dtype=np.float32)
        self._x_std = np.asarray(meta["x_std"], dtype=np.float32)
        self._y_mean = np.asarray(meta["y_mean"], dtype=np.float32)
        self._y_std = np.asarray(meta["y_std"], dtype=np.float32)
        if self._x_mean.shape != (len(names),) or self._y_mean.shape != (
            len(self._layout.pressure),
        ):
            raise ValueError("scaler sizes do not match the sensor layout")

        tf = _tensorflow()
        self._model = build_gru_model(
            self._window, len(names), len(self._layout.pressure), int(meta["units"])
        )
        self._model.load_weights(str(self._dir / WEIGHTS_FILE))

        def forward(x: Any) -> Any:
            return self._model(x, training=False)

        # Graph mode: one call per 5-minute window costs a few ms instead of ~300 ms eagerly.
        self._infer = tf.function(
            forward,
            input_signature=[tf.TensorSpec([None, self._window, len(names)], tf.float32)],
            autograph=False,
        )
        self.artefact_sha256 = artefact_sha256
        self._info = ModelInfo(
            name=MODEL_NAME,
            version=versioned(str(meta["version"]), artefact_sha256),
            is_mock=False,
        )

    @property
    def info(self) -> ModelInfo:
        return self._info

    @property
    def min_history_windows(self) -> int:
        return self._window + self._horizon - 1

    @property
    def layout(self) -> SensorLayout:
        return self._layout

    @property
    def keras_model(self) -> Any:
        return self._model

    def predict(self, request: ForecastRequest) -> ForecastResult:
        to_model = self._ids.mapping(request.pressure_sensor_ids)
        unknown = [s for s, m in to_model.items() if m not in self._layout.pressure]
        if unknown:
            raise ForecastError(f"sensors not predicted by this model: {unknown}")
        needed = self.min_history_windows
        if len(request.history) < needed:
            raise ForecastError(f"needs {needed} windows of history")
        end = len(request.history) - (self._horizon - 1)
        np = self._np
        try:
            steps = [
                gru_step_features(self._ids.window(w), self._layout)
                for w in request.history[end - self._window : end]
            ]
        except KeyError as exc:
            raise ForecastError(f"history is missing an input sensor: {exc}") from exc
        x = (np.asarray(steps, dtype=np.float32) - self._x_mean) / self._x_std
        y = self._infer(x[None, ...]).numpy()[0] * self._y_std + self._y_mean
        by_sensor = {s: float(v) for s, v in zip(self._layout.pressure, y, strict=True)}
        return ForecastResult(
            timestamp=request.target_timestamp,
            expected_pressures={s: by_sensor[m] for s, m in to_model.items()},
            model=self._info,
        )
