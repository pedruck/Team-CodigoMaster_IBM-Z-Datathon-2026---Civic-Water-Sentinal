# P2 — Forecasting (ML owner)

The forecaster turns a 5-minute window (and its history) into the **expected pressure of every
pressure sensor**. The detector (P4) compares it with the observation. This document is the
entry point for the ML part: models, artefacts, how to train them, how the backend loads them,
and the answers to the ML questions in `docs/questions-for-team.md`.

## Model ladder (design doc §3, Table 2)

| Step | Model | Code (runtime) | Notebook (training) | Runs on LinuxONE with |
|---|---|---|---|---|
| 1 | Hour-of-week baseline | `forecasting/baseline.py` | `notebooks/p2_forecast/01_baseline.ipynb` | plain Python |
| 2 | XGBoost, one model per sensor | `forecasting/xgb.py` | `notebooks/p2_forecast/02_xgboost_snapml.ipynb` | Snap ML (`tree_format="auto"` → zDNN on Telum, compressed trees on CPU) |
| 3 | GRU, all 33 sensors | `forecasting/gru.py` | `notebooks/p2_forecast/03_gru_keras_zdnn.ipynb` | TensorFlow + `ibm-zdnn-plugin` (organisers' image); ONNX for zDLC |

Every step must beat the previous one on the 2018 validation period (Nov–Dec); 2019 is never
read by P2 (`p2lib.load_scada` refuses it).

The notebooks follow the organisers' examples (`Loan_Approval_Demo`, `Churn_Scikit_PyTorch`,
`Fraud_LSTM_Keras_TF`, `Digit_Class_TensorFlow`): pandas from CSV → train → evaluate → export a
portable format → accelerator check (`ibm-zdnn-plugin`, `_z_ops.so`, NNPA) → latency benchmark
(median / best / p99, batch throughput).

## Features (single source)

`forecasting/features.py` defines the features once. The notebooks build them vectorised
(`notebooks/p2_forecast/p2lib.py`) and **assert row by row that both give the same numbers**
before training, so training and inference cannot drift apart.

- **XGBoost** (per target sensor): inlet/pump flows, tank level, hour, day of week and the
  *other* 32 pressure sensors at the same instant. Never the target's own reading.
- **GRU** (per timestep): all pressures, flows, tank level, cyclic calendar (sin/cos of hour and
  of week). Reads `request.history` only, never the window being scored.

Sensor IDs are the data owner's column names (`Pressure_n1`, `Flow_p227`, `Level_T1`), the same
IDs the backend reads from `data/vN/sensors.csv`.

## Training choices

- **Leak-aware training**: windows inside a leak of the 2018 report (`leaks_2018.csv`,
  `start ≤ t ≤ end`) are excluded (baseline, XGBoost) or weighted 0 (GRU).
- **Split**: fit Jan–Oct 2018, early-stop / validate Nov–Dec 2018, then refit on all leak-free
  2018 windows (baseline, XGBoost).
- **σ per sensor** (`sigma_m` in every model's metadata): 1.4826·MAD of the validation residuals.
  This is the σ of `z = (expected − observed) / σ` for P4.
- **Leak contrast** (in every model's metadata): mean |z| inside known 2018 leaks divided by mean
  |z| outside, on validation. Higher means leaks stand out more. It is reported next to MAE
  because a lower MAE alone can mean the model *follows* the leak (see risks).

## Artefacts (outside git)

Under `shared/civicwater/models/` on the VM (`SENTINEL_MODELS_DIR`):

| Model | Path | Contents |
|---|---|---|
| Baseline | `baseline-hourofweek@0.1.0.json` | profile (168 h × sensor), `sigma_m`, validation metrics |
| XGBoost | `xgboost-33@0.1.0/` | `meta.json`, one portable XGBoost JSON per sensor, parity sample |
| GRU | `gru-33@0.1.0/` | `meta.json` (window, horizon, scalers), `model.weights.h5`, `model.onnx`, parity sample |

Each `meta.json` records data version, training machine, validation metrics and, after Part B of
the notebooks runs on the VM, the measured latency and the x86 ↔ s390x parity per machine.

## Platform notes (found while building this)

- **Snap ML 1.17 cannot read XGBoost 3.x JSON as written**: XGBoost 3 stores `base_score` as a
  vector (`"[8.2E-1]"`) and Snap ML fails with "no objective defined". `portable_booster_json`
  rewrites it as a scalar (lossless for single-target regression; XGBoost still loads it). The
  export uses it and `XGBoostForecaster` applies it defensively at load time.
- **tf2onnx + Keras 3 does not emit an ONNX `GRU` node** (it becomes a generic `Loop`), so zDLC
  could not map it to the NNPA as one op. `export_onnx` rebuilds the same network as
  `torch.nn.GRU` (same maths as Keras `reset_after=True`) and exports one `GRU` node with
  `linear_before_reset=1`. Tests check the node and compare the ONNX output (ONNX reference
  evaluator) with Keras.
- **Eager Keras is slow per call**: one GRU forecast took ~300 ms eagerly on x86 and ~5 ms in
  graph mode; `GRUForecaster` uses `tf.function`.
- TreeSHAP contributions use the native XGBoost booster (Snap ML has no `pred_contribs`); they
  dominate XGBoost latency per window and can be turned off (`with_contributions=False`).

## Backend wiring (for P3)

```python
from sentinel.forecasting.registry import load_forecaster
forecaster = load_forecaster("xgboost", "/home/ibm-user/shared/civicwater/models/xgboost-33@0.1.0")
app = create_app(forecaster=forecaster)
```

`SENTINEL_FORECASTER` currently accepts only `mock` (`config.py`, P3). Proposal: accept
`baseline | xgboost | gru` plus `SENTINEL_FORECASTER_PATH`, and call `load_forecaster` at
startup (fail fast). The API process must use the VM image's Python (`/opt/venv`), where
numpy, xgboost, snapml and tensorflow are installed.

## Answers to the ML questions (`docs/questions-for-team.md`)

| ID | Answer |
|---|---|
| Q-ML-01 | Accepted as implemented: one call per window, all sensors in `pressure_sensor_ids`. |
| Q-ML-02 | Yes, XGBoost uses the other sensors' current pressures (design doc), never the target's own. The masking risk is real and is measured by the leak contrast; see risks. |
| Q-ML-03 | XGBoost JSON → Snap ML; GRU Keras weights → TensorFlow (+ zDNN plugin), ONNX kept for zDLC. Loaded once at startup via `load_forecaster`; errors at load time are fatal. |
| Q-ML-04 | Same process as FastAPI, VM image Python. No onnxruntime wheel exists for s390x; TensorFlow is in the organisers' image. |
| Q-ML-05 | Measured by Part B of notebooks 02/03 and stored in `meta.json` per machine; all far below the 300 s window. |
| Q-ML-06 | baseline 1, XGBoost 1, GRU `window + horizon − 1` (143 + horizon with the 12 h default; ≤ 288). |

## Risks

- **Models that see recent pressures can follow a leak.** XGBoost reads neighbouring sensors at
  the same instant; the GRU with `horizon=1` reads the last 12 h up to the previous window. Both
  can absorb a persistent pressure drop and shrink the residual. Compare the leak contrast of
  each step (and of the GRU with a larger `HORIZON`) before choosing the model P4 uses.
- The data is simulated (BattLeDIM); none of this is validated on a real network.
- The NNPA computes in reduced precision; the parity check reports the difference instead of
  asserting bit equality on s390x.
- Verified so far on x86 with synthetic data in the v1 format (code paths, parity, export).
  Real numbers come from running the notebooks on the real `data/v1` and Part B on the VM.
