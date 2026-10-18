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
read by P2 (`training.load_scada` refuses it).

The notebooks follow the organisers' examples (`Loan_Approval_Demo`, `Churn_Scikit_PyTorch`,
`Fraud_LSTM_Keras_TF`, `Digit_Class_TensorFlow`): pandas from CSV → train → evaluate → export a
portable format → accelerator check (`ibm-zdnn-plugin`, `_z_ops.so`, NNPA) → latency benchmark
(median / best / p99, batch throughput).

## Features (single source)

`forecasting/features.py` defines the features once. `forecasting/training.py` builds them
vectorised with pandas for the notebooks, which **assert row by row that both give the same
numbers** before training, so training and inference cannot drift apart. `training.py` is
type-checked and tested in CI (`tests/test_forecasting_training.py`); inference never imports it.

- **XGBoost** (per target sensor): inlet/pump flows, tank level, hour, day of week and the
  *other* 32 pressure sensors at the same instant. Never the target's own reading.
- **GRU** (per timestep): all pressures, flows, tank level, cyclic calendar (sin/cos of hour and
  of week). Reads `request.history` only, never the window being scored.

## Sensor IDs

The team uses one registry (PR #4): `config/sensors.battledim.json`, with network-element IDs
(`n1`, `p227`, `PUMP_1`, `T1`). Training reads the same variable as the API
(`SENTINEL_SENSOR_REGISTRY`, default that file) and stores those IDs in the model. The SCADA
CSV columns use another spelling (`Pressure_n1`, `Flow_p227`, `Level_T1`); `IdTranslator` maps
either spelling onto the model's IDs, so notebooks and CSV-based tools still work.

## Training choices

- **Leak-aware training with P1's mask** (`sentinel.data.clean_mask`, `docs/data.md` §7), default
  policy `reparados` (43.5% of 2018 on the real data): the periods of the 10 leaks repaired in
  2018 are excluded; the 4 unrepaired incipient leaks stay, as part of the "normal" state.
  Excluding every active leak (`todos`) would leave 2.1% of the year. The GRU only learns from
  targets whose whole input window is clean (`training.sequence_weights`).
- **Split**: fit Jan–Oct 2018, early-stop / validate Nov–Dec 2018, then refit on all leak-free
  2018 windows (baseline, XGBoost).
- **σ per sensor** (`sigma_m` in every model's metadata): 1.4826·MAD of the validation residuals.
  This is the σ of `z = (expected − observed) / σ` for P4.
- **Small σ, and P4's σ floor**: `n215` and `n740` barely move (`docs/data.md` §11), so a tiny σ
  inflates z. The notebooks list sensors with σ < 0.02 m (`training.low_variance_sensors`).
  First run on the real `data/v1` (VM, 2026-10-17): the baseline gets **σ = 0 on `n215`** (its
  residuals are mostly identical, so the MAD is zero), and XGBoost has σ < 0.02 m on **32 of 33
  sensors** (3–12 mm) because its error is that small. P4 therefore needs a σ floor for every
  sensor, not only `n215`/`n740`. The leak contrast leaves out sensors with σ = 0.
- **Validation MAE** is computed on clean windows only (normal operation). Including leak
  windows would reward a model that follows the leak, because it errs less exactly there.
- **Leak contrast** (in every model's metadata): the pressure-drop score `max(z, 0)`, with
  `z = (expected − observed) / σ` as P4 uses it, averaged over sensors, in windows the mask marks
  as leak-affected, divided by the same in clean windows, on validation. Only drops count, as in
  the detector: a residual pushed the wrong way during a leak (pressure above expected) adds
  nothing. Above 1, leaks show as drops; about 1, they do not stand out; below 1, the model moves
  the residual the wrong way. It is reported next to MAE because a lower MAE alone can mean the
  model *follows* the leak (see risks).
- Metadata written with these two definitions has `validation.metrics_version = 2`. Notebook 02
  warns when the baseline it compares with is older, and the ladder table at the end of
  notebook 03 marks such models `old: re-run`.

## Results on the real data and the choice (2026-10-17)

Notebooks 01–03 on the real `data/v1` 2018 export, run on the LinuxONE VM, metrics v2
(validation Nov–Dec 2018; latency = one window, 33 sensors, p50):

| Model | MAE on clean windows | Leak contrast | Latency p50 |
|---|---|---|---|
| Hour-of-week baseline | 0.105 m | 4.16 | 0.025 ms |
| **XGBoost (Snap ML)** | **0.006 m** | 3.39 | 0.64 ms (134 ms with TreeSHAP) |
| GRU (run mode not recorded) | 0.077 m | 1.80 | 10.2 ms |

**Chosen model: XGBoost** (`xgboost-33@0.1.0`, 600-tree run, trained on s390x). It forecasts
normal operation 17× better than the baseline, leaks still stand out clearly as pressure drops
(contrast 3.39) and each forecast comes with TreeSHAP contributions for the operator. The
baseline has the higher contrast because it reads no sensor, so it cannot follow a leak;
XGBoost absorbs part of the drop through the neighbouring sensors. The GRU (horizon 1) follows
leaks the most and stays optional.

The contrast compares means: it does not measure false alarms or detection delay. The final
check is P4's detector on these residuals (P6 on 2019); before that, a CUSUM on the 2018
validation residuals of each model would compare detected leaks, delay and false alarms.

σ: XGBoost has σ < 0.02 m on 32 of 33 sensors (3–12 mm) and the baseline σ = 0 on `n215`;
P4 needs a σ floor for every sensor (see Training choices).

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
- TreeSHAP contributions use the native XGBoost booster (Snap ML has no `pred_contribs`). On
  the VM they take ~127 of the ~134 ms per window; `load_forecaster("xgboost", path,
  with_contributions=False)` turns them off (≈ 67 s instead of ≈ 3.9 h of forecasting for a
  full-year replay, estimated).
- **Snap ML on the NNPA was slower than on CPU for this model**: 1.35 vs 0.64 ms per window,
  5.4 vs 1.2 ms per 1000 rows (`docs/performance.md`). `tree_format="auto"` picks the NNPA
  (`zdnn_tensors`) on the VM; `tree_format="compress_trees"` is the fastest measured option.
- **snapml lives in the container's `/opt/venv`**: it disappeared once when the container was
  recreated. Check with `python -c "import snapml"`; reinstall with
  `pip install --prefer-binary snapml`.

## Backend wiring (for P3)

```python
from sentinel.config import Settings
from sentinel.forecasting.registry import load_forecaster
from sentinel.main import build_pipeline, create_app

settings = Settings.from_env()
forecaster = load_forecaster(
    "xgboost",
    "/home/ibm-user/shared/civicwater/models/xgboost-33@0.1.0",
    with_contributions="on_alert",  # TreeSHAP only for an alert's sensors; "all" | "off"
)
app = create_app(settings, build_pipeline(settings, forecaster=forecaster))
```

Wired by P3 in BE-011: `SENTINEL_FORECASTER=baseline | xgboost | gru` plus
`SENTINEL_FORECASTER_PATH`, `SENTINEL_FORECASTER_CONTRIBUTIONS`, `SENTINEL_FORECASTER_ENGINE`
and `SENTINEL_FORECASTER_TREE_FORMAT`; `build_pipeline` calls `load_forecaster` at startup
(fail fast, `.env.example`, `docs/runbook-vm.md`). The API process must use the VM image's Python (`/opt/venv`), where
numpy, xgboost, snapml and tensorflow are installed. Off the VM, `uv sync --extra ml` (or
`pip install -e ".[ml]"`) installs numpy, xgboost and snapml (DEP-ML-03); on s390x only snapml
has a PyPI wheel, so keep the image's `/opt/venv` there.

**TreeSHAP on demand** (`SENTINEL_FORECASTER_CONTRIBUTIONS=on_alert`, the default): the forecast
carries no contributions; after detection the pipeline calls `XGBoostForecaster.explain` with
the same request and the alert's deviating sensors (optional `ExplainingForecaster`, contract
§2, proposed to P3). Same numbers as `all`, paid only when an alert opens and only for its
sensors (≈ 3.9 ms per sensor estimated from the VM measurement, not measured). `all` keeps
TreeSHAP for every sensor of every window; `off` (or `false`) turns it off.

## Out-of-fold residuals for the detector (DEP-DET-05)

The CUSUM calibration needs residuals of the served model on windows it did not train on.
`sentinel.forecasting.oof` produces them for 2018:

- **Folds = the 12 calendar months.** For each month, one XGBoost per sensor is trained on the
  clean windows (P1 mask, the delivered model's policy `reparados`) of the other 11 months and
  predicts every window of the held-out month, leak periods included. Each labelled leak is
  predicted by models that never saw its month (`tests/test_forecasting_oof.py` checks it).
- **No early stopping in the folds:** trees per sensor = the delivered model's
  `best_iteration`, same parameters, so the predicted month never tunes its own model.
- **Two variants (Q-ML-07):** `with-neighbours` = `xgboost-33@0.1.0` as delivered;
  `without-neighbours` = flows, tank level and calendar only (its trees come from the
  notebook 02 protocol: fit Jan–Oct, early-stop Nov–Dec). If the detector owner finds
  "without" better on the delay × false-alarm curve, it is exported as a new model and needs
  its own calibration.
- **Output** per variant in `$SENTINEL_DATA_DIR/v1/oof/xgboost-33@0.1.0/<variant>/` (outside
  git): `residuals_long.csv.gz` (`timestamp, sensor, observed, expected, fold`; residual =
  observed − expected; values with 6 decimals), `folds.json` (month → first/last window) and
  `declaration.json` (inputs the expected value depends on, training mask, trees, hashes,
  sha256 of the uncompressed CSV text, and MAE / σ / leak contrast under the `reparados` and
  `reparados+crescimento` masks, informative only). Finished folds are cached in `.folds/`,
  so an interrupted run resumes.

Run on the VM (≈ 30–60 min with 3 cores; a fit with 600 trees took ≈ 4 s per sensor on 2 x86
cores, 33 sensors × 12 folds for the first variant, ≈ 1 s for the second):

```bash
# JupyterLab terminal, inside your clone of the repository (the image's python, /opt/venv)
PYTHONPATH=src python -m sentinel.forecasting.oof --fast \
    --model-dir ~/shared/civicwater/models/xgboost-33@0.1.0     # smoke test, writes oof/fast/
PYTHONPATH=src nohup python -m sentinel.forecasting.oof \
    --model-dir ~/shared/civicwater/models/xgboost-33@0.1.0 > ~/oof.log 2>&1 &
tail -f ~/oof.log
```

The calibration mask is the detector owner's choice (the P1 handoff recommends
`reparados+crescimento` for σ and h); the out-of-fold models keep the delivered model's
training mask, so the forecaster does not need retraining.

## Answers to the ML questions (`docs/questions-for-team.md`)

| ID | Answer |
|---|---|
| Q-ML-01 | Accepted as implemented: one call per window, all sensors in `pressure_sensor_ids`. |
| Q-ML-02 | Yes, XGBoost uses the other sensors' current pressures (design doc), never the target's own. The masking risk is real and is measured by the leak contrast; see risks. |
| Q-ML-03 | XGBoost JSON → Snap ML; GRU Keras weights → TensorFlow (+ zDNN plugin), ONNX kept for zDLC. Loaded once at startup via `load_forecaster`; errors at load time are fatal. |
| Q-ML-04 | Same process as FastAPI, VM image Python. No onnxruntime wheel exists for s390x; TensorFlow is in the organisers' image. |
| Q-ML-05 | Measured on the VM (`docs/performance.md`): XGBoost p50 0.64 ms per window with Snap ML, 134 ms with TreeSHAP; no warm-up needed beyond loading at startup (cold-start cost not measured). All far below the 300 s window. |
| Q-ML-06 | baseline 1, XGBoost 1, GRU `window + horizon − 1` (143 + horizon with the 12 h default; ≤ 288). |
| DEP-ML (data) | Mask, registry and v1 files consumed exactly as documented by P1 (`docs/data.md`). |

## Risks

- **Models that see recent pressures can follow a leak.** XGBoost reads neighbouring sensors at
  the same instant; the GRU with `horizon=1` reads the last 12 h up to the previous window. Both
  can absorb a persistent pressure drop and shrink the residual. Compare the leak contrast of
  each step (and of the GRU with a larger `HORIZON`) before choosing the model P4 uses.
- The data is simulated (BattLeDIM); none of this is validated on a real network.
- The NNPA computes in reduced precision; the parity check reports the difference instead of
  asserting bit equality on s390x.
- Measured on the real `data/v1` on the VM (2026-10-17). The x86 → s390x prediction parity is
  not measured yet: the chosen model was trained on the VM, so its parity sample compares s390x
  with itself. Train Part A off-platform (Colab) to measure it.
