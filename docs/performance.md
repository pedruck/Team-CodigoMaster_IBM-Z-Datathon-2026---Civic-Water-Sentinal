# Performance

Goal from the design document (§7): p50/p99 latency per window **measured on LinuxONE**.
All numbers so far use the **mock** forecaster/detector, so they measure backend overhead
only, not model inference. A LinuxONE (s390x) baseline exists since 2026-10-17 (below).

## What is measured

| Metric | Where | How |
|---|---|---|
| Per-stage latency: `validation`, `temporal_policy`, `history`, `forecast`, `detection`, `audit`, `total` | `Pipeline` (`StageTimer`) | `time.perf_counter`, ms, last `SENTINEL_METRICS_SAMPLE_SIZE` samples, nearest-rank p50/p95/p99 |
| `http_post_windows` | HTTP middleware | request in → response out, includes JSON parsing/Pydantic validation/serialisation |
| Counters, throughput, error rate | `/v1/status` | accepted windows / uptime; 4xx+5xx / `POST /v1/windows` requests |
| Client-side latency | `scripts/replay.py` | wall time per POST (new TCP connection per request, stdlib `urllib`) |

Pydantic shape validation happens inside FastAPI before the handler, so it is not in the
`validation` stage (which covers registry + time grid); it is included in
`http_post_windows`.

## Baseline 2026-10-17 — x86_64 dev machine, mocks

Environment: Python 3.12.13, Linux 7.2.5 x86_64 (developer laptop, other processes running),
FastAPI 0.143.3, Pydantic 2.14.1, uvicorn 0.54.0, single worker. Defaults: capacity 288,
min history 144, 33/3/1 placeholder sensors.

### In-process pipeline (`just bench 5000`, in-memory audit)

5000 windows (144 `insufficient_history`, 4856 `scored`):

| Stage | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|
| validation | 0.0184 | 0.0301 | 0.0388 | 0.163 |
| temporal_policy | 0.0526 | 0.0869 | 0.1139 | 0.3493 |
| history | 0.0043 | 0.0071 | 0.0102 | 0.0326 |
| forecast (mock) | 0.0712 | 0.1089 | 0.1356 | 0.2309 |
| detection (mock) | 0.1544 | 0.2489 | 0.308 | 1.3673 |
| audit (memory) | 0.0216 | 0.0398 | 0.0773 | 3.5587 |
| **total** | **0.3707** | 0.5878 | **0.7358** | 3.9623 |

### HTTP replay (uvicorn on 127.0.0.1, `scripts/replay.py`, 576 synthetic windows)

| Measure | Value |
|---|---|
| status codes | 576 × 201; resend of 5 windows → 5 × 200 |
| client throughput | 136.89 windows/s (sequential, one connection per request) |
| client latency p50 / p99 / max | 6.797 / 14.837 / 61.685 ms |
| server `http_post_windows` p50 / p99 | 3.9639 / 8.3455 ms |
| server pipeline `total` p50 / p99 | 0.8037 / 2.1466 ms |

Context: a window arrives every 300 s (300 000 ms); the measured client-side p99 (14.8 ms)
is more than four orders of magnitude below that interval. Real model latency is unknown (DEP-ML-04).

## Baseline 2026-10-17 — LinuxONE VM (s390x), mocks

Environment: team VM `ibmdatathon` (access in the team channel, not in this repo), s390x,
big-endian, `/proc/cpuinfo` machine type 9175, 3 vCPUs, 7.8 GiB RAM, Ubuntu 24.04.5 LTS,
kernel 6.8.0-147, system Python 3.12.3, uv 0.11.16, same locked runtime versions as x86
(FastAPI 0.143.3, Pydantic 2.14.1 / pydantic-core 2.50.1, Starlette 1.8.0, uvicorn 0.54.0).
Commit `51d2984`. `nnpa` is listed in `/proc/cpuinfo` (Telum AI accelerator exposed to the
VM); nothing in the backend uses it — it matters for the model runtimes (design doc §5).

### In-process pipeline (`scripts/bench_pipeline.py --windows 5000`, in-memory audit)

| Stage | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|
| validation | 0.0082 | 0.009 | 0.0114 | 0.7324 |
| temporal_policy | 0.0223 | 0.0243 | 0.0339 | 6.1092 |
| history | 0.0015 | 0.0018 | 0.0024 | 0.0182 |
| forecast (mock) | 0.0311 | 0.0324 | 0.0394 | 0.9434 |
| detection (mock) | 0.0699 | 0.0744 | 0.1094 | 2.0786 |
| audit (memory) | 0.0086 | 0.0108 | 0.0221 | 1.627 |
| **total** | **0.1645** | 0.1846 | **0.3514** | 13.7636 |

### HTTP replay on the VM (server and client on localhost, 576 synthetic windows)

| Measure | Value |
|---|---|
| status codes | 576 × 201; resend of 5 windows → 5 × 200; error rate 0.0 |
| alerts | 1 (`A-20190102T0920Z-1`, same ID as the x86 run on the same data) |
| client throughput | 356.31 windows/s (sequential, one connection per request) |
| client latency p50 / p99 / max | 1.419 / 17.988 / 36.918 ms |
| server `http_post_windows` p50 / p99 | 0.8502 / 15.4848 ms |
| server pipeline `total` p50 / p99 | 0.193 / 0.539 ms |

### Real data shape: full 2019 v1 export, in process (2026-10-17)

`python scripts/validate_v1_ingestion.py $SENTINEL_DATA_DIR/v1 --year 2019` (script later
replaced by `scripts/validate_replay_ingestion.py`, which reads the replay JSONL) in the shared
Jupyter container on the LinuxONE VM (`/opt/venv`, Python 3.12.15, same pydantic/fastapi/
starlette/uvicorn versions as `uv.lock`), commit `c70f997`, `nice -n 10`, VM otherwise idle
(load ≈ 0.05). Mock forecaster/detector, in-memory audit, official 37-sensor registry.

| Measure | Value |
|---|---|
| windows | 105 120 read · 0 rejected · 144 `insufficient_history` · 104 976 `scored` |
| wall time (CSV read + parse + pipeline) | 30.27 s |
| Pydantic window parsing, mean | 0.0228 ms |
| pipeline `total` (last 10 000 windows) p50 / p99 / max | 0.1988 / 0.4044 / 3.5022 ms |

An earlier identical run (pre-commit copy of the script) gave 29.39 s and p50/p99
0.1961/0.2843 ms. At `073c605` (naive CSV timestamps normalised to UTC in ingestion): 32.99 s,
p50/p99 0.2231/0.4074 ms, max 13.6681 ms. Single runs on a shared VM vary; treat these as
orders of magnitude, not as the cost of the timezone normalisation.

### Full 2019 over HTTP into the shared API (2026-10-17)

Shared integration API (`scripts/shared_api.sh start`, `main` @ `fcce163`, official registry,
in-memory audit, mocks, one worker, BLAS threads = 1) in the `notebook` container on the
LinuxONE VM. Client: `scripts/replay.py` with the data owner's
`$SENTINEL_DATA_DIR/replay/v1/windows_2019.jsonl`, same container, `127.0.0.1:8000`,
`nice -n 10`, sequential, a new connection per request. VM load before: 0.22 (other members'
Jupyter kernels open, idle).

| Measure | Value |
|---|---|
| windows | 105 120 sent · 105 120 × `201` · error rate 0 · 144 `insufficient_history`, 104 976 `scored` |
| wall time / client throughput | 145.238 s / 723.78 windows/s |
| client latency p50 / p99 / max | 1.284 / 2.861 / 144.338 ms |
| server `http_post_windows` p50 / p95 / p99 (last 10 000) | 0.8826 / 1.2801 / 2.1702 ms |
| pipeline `total` p50 / p95 / p99 (last 10 000) | 0.2678 / 0.3967 / 0.5581 ms |
| `validation` / `temporal_policy` / `forecast` / `detection` / `audit` p50 | 0.0422 / 0.035 / 0.0381 / 0.0897 / 0.0141 ms |
| audit backlog after the run | 0 events |
| API log size | 26.5 MB for the year (one INFO line per window) |

### Full 2019 over HTTP into the shared API with the real XGBoost forecaster (2026-10-17)

Same procedure, `main` @ `333b1bd`, `SENTINEL_FORECASTER=xgboost` (`xgboost-33@0.1.0`,
`SENTINEL_FORECASTER_CONTRIBUTIONS=false`, `SENTINEL_FORECASTER_TREE_FORMAT=compress_trees`),
mock detector, `SENTINEL_LOG_LEVEL=WARNING`, client `nice -n 10` in the same container.

| Measure | Value |
|---|---|
| windows | 105 120 × `201` · error rate 0 · 144 `insufficient_history`, 104 976 `scored` · 0 alerts |
| wall time / client throughput | 246.99 s / 425.6 windows/s |
| client latency p50 / p99 / max | 1.96 / 11.17 / 204.514 ms |
| server `http_post_windows` p50 / p95 / p99 / max (last 10 000) | 1.5679 / 2.532 / 5.8296 / 84.6257 ms |
| pipeline `total` p50 / p95 / p99 (last 10 000) | 0.9355 / 1.1867 / 1.5438 ms |
| `forecast` / `detection` p50 (last 10 000) | 0.6903 / 0.0921 ms |
| audit backlog after the run | 0 events |

The forecast stage matches the in-process run (0.651 ms p50, `compress_trees`); the extra
~0.67 ms of `total` p50 over the mock run is the model. One run, shared VM, not a benchmark.

### Full 2019 over HTTP, real stack + audit chain, private API on the VM (BE-013, 2026-10-18)

`main` + PR #18 + BE-013 (`e8be180`, local integration bundle) in the P3 clone, uvicorn on
127.0.0.1:8003 in the `notebook` container: `xgboost-33@0.1.0` (`compress_trees`,
contributions `on_alert`), `SENTINEL_DETECTOR=cusum` with `config/calibration.xgboost-33.json`,
`config/regions.battledim.json` and the data owner's `l-town.json`, `SENTINEL_AUDIT_SINK=chain`
(file in the container's `/tmp`, fsync per record), `SENTINEL_NARRATOR=none`,
`SENTINEL_LOG_LEVEL=WARNING`; `scripts/replay.py` with `nice -n 10`.

| Measure | Value |
|---|---|
| windows | 105 120 accepted · 144 `insufficient_history`, 104 976 `scored` · audit backlog 0 |
| wall time / client throughput | 348.46 s / 301.67 windows/s (425.6 with the mock detector and the in-memory sink, above) |
| models | forecaster and detector `is_mock: false`, `mocks_active: false` |
| alerts | 895, all with `forecast_contributions_m`, `region` and `candidate_pipe` |
| audit chain | 106 015 records (105 120 windows + 895 alerts), `verify_file` ok, the same over `GET /v1/audit` pages; restart with the same run continues at seq 106 014 |
| explanations | 895 records (`template`, no narrator), `verify_file` ok; `verify_alert` of an alert against its record ok |

Alert volume (counted from the audit log; the 2019 labels were **not** read): Jan–Mar 10 alerts,
then about 100 per month (Apr 59 … Dec 111); 492 distinct (region, `suspected_onset`) pairs,
up to 28 alerts for one pair; 37 % of consecutive alerts within 30 min. Whether these are leaks or
false alarms is the layer-2 evaluation's job (EV-003, ADR-010); the repeated alerts for one
episode are a question for the detection owner (Q-DET-09). The slowdown versus the mock run is not
broken down (no per-stage numbers were captured in this run). One run, shared VM, not a benchmark.

### Full 2019 into the shared API with the real stack and the audit chain (2026-10-18)

`main` @ `45f733d` in `~/shared/civicwater/main`, started by `scripts/shared_api.sh` with the
configuration of the run above (XGBoost `compress_trees` / `on_alert`, CUSUM v1, regions, network)
and `SENTINEL_AUDIT_SINK=chain SENTINEL_AUDIT_RUN=replay-2019-1018`: the chain is on `~/shared`
(fsync per record). Client `scripts/replay.py`, `nice -n 10`, same container.

| Measure | Value |
|---|---|
| windows | 105 120 × `201` · 144 `insufficient_history`, 104 976 `scored` · audit backlog 0 |
| wall time / client throughput | 338.04 s / 310.97 windows/s |
| client latency p50 / p99 / max | 2.921 / 7.242 / 72.126 ms |
| server `http_post_windows` p50 / p95 / p99 (last 10 000) | 2.462 / 2.9244 / 21.3441 ms |
| pipeline `total` p50 / p95 / p99 (last 10 000) | 1.8279 / 2.1888 / 20.6283 ms |
| `forecast` / `detection` / `audit` p50 (last 10 000) | 0.6989 / 0.1752 / 0.7918 ms |
| `explain` (TreeSHAP for the deviating sensors, 895 alert windows) p50 / p95 / p99 | 17.805 / 29.3036 / 39.1651 ms |
| alerts / audit records / explanations | 895 / 106 015 / 895 — the same counts as the private run above; both files `verify_file` ok |

The fsync of the chain on `~/shared` costs 0.79 ms p50 per window, about the forecast's cost;
the p99 of `total` (~20 ms) follows the alert windows, where `explain` runs. Chain anchor at the
end of the run: seq 106 014, hash
`96fa38333d56681f5de7aa09e0ce46d42977e712869b7ed951cb02c6a15b0856` (explanations: seq 894,
`2c93bfe6c39709e3faae9b704607b1dd917ea02079c3efbb5ee393edbed97b03`). One run, shared VM.

### Same, after BE-024: pinned model, write token, limits (2026-10-18)

`main` @ `6b9817a`, same configuration plus `SENTINEL_FORECASTER_SHA256` (served
`0.1.0+808746bc`) and the write token; run `replay-2019-20261018-be024`; `scripts/replay.py
--token-file`.

| Measure | Value |
|---|---|
| windows | 105 120 × `201` (none outside the physical limits) · audit backlog 0 |
| wall time / client throughput | 334.05 s / 314.69 windows/s |
| client latency p50 / p99 / max | 2.915 / 6.287 / 70.837 ms |
| alerts / explanations | 895 / 895 (`verify_file` ok) |
| `replay_check` chain vs `windows_2019.jsonl` | ok: 105 120 / 105 120 matched, 0 mismatched, 0 foreign |
| `POST` without the token | 401 |

Chain anchor at the end of the run: seq 106 014, hash
`892c096e50a57b92d320f78259ae31f6ea254b0728d8eddfe5a738a80372c8f6` (explanations: seq 894,
`465f6727a54b65eccfe32b2b702e608d721679adc70628e87c2f783f122bc470`). The guard and the limits
cost nothing visible next to the run without them (338.04 s). One run, shared VM.

### x86 vs s390x parity (design doc §7, byte order)

`SensorWindow.content_hash()` of `docs/examples/window.valid.json`:
`7fc91e5ac9bb54402ce4577df7f97f1df636ad01c84005f7de2b0ec5189717fa` on both x86_64 and
s390x. Same alert ID and status sequence for the same synthetic replay. This covers the
backend only; the model parity test (same predictions on both platforms) is still open
(DEP-ML-03): the first real model was trained on the VM itself, see the next section.

## Forecaster on the LinuxONE VM — `xgboost-33@0.1.0` (P2, 2026-10-17)

Model: 33 per-sensor XGBoost models (at most 600 trees each, early-stopped on Nov–Dec 2018),
trained on the VM (s390x) on the real `data/v1` 2018 export, branch `feat/p2-forecasting`.
Measured by Part B of `notebooks/p2_forecast/02_xgboost_snapml.ipynb` with
`training.benchmark` (3 warm-up calls, then 100 timed calls) and stored in the model's
`meta.json`. Thread limits of the kernel were not recorded.

**One forecast = one window, 33 sensors** (`XGBoostForecaster.predict`, what the API calls):

| Engine | p50 ms | p99 ms | best ms |
|---|---|---|---|
| xgboost native (CPU) | 7.088 | 8.197 | 6.836 |
| Snap ML `compress_trees` (CPU) | **0.637** | 0.812 | 0.628 |
| Snap ML `zdnn_tensors` (NNPA, Telum) | 1.345 | 2.256 | 1.326 |
| Snap ML `auto` + TreeSHAP contributions (API default) | 134.472 | 140.015 | 125.819 |

With `tree_format="auto"` Snap ML picked `zdnn_tensors` on the VM.

**Batch throughput, one sensor model, 1000 rows** (20 timed calls, p50):

| Engine | ms / 1000 rows | rows/s |
|---|---|---|
| xgboost native (CPU) | 2.62 | 381 574 |
| Snap ML `compress_trees` (CPU) | **1.20** | 830 138 |
| Snap ML `zdnn_tensors` (NNPA) | 5.44 | 183 859 |
| Snap ML `auto` (= `zdnn_tensors`; no TreeSHAP in this test) | 5.45 | 183 466 |

Readings:
- Snap ML on CPU is 11× faster than native XGBoost per window and 2.2× in batch.
- The NNPA path is slower than Snap ML on CPU for this model, both per window and for 1000
  rows. Larger batches were not tested.
- TreeSHAP (native XGBoost `pred_contribs`; Snap ML has none) is ~127 of the ~134 ms.
  Estimate, not measured: a full-year replay (105 120 windows) spends ≈ 3.9 h in the
  forecaster with contributions and ≈ 67 s without them (`compress_trees`).
- Everything is far below the 300 s interval.
- Parity: the parity sample was produced on s390x too, so `meta.json` → `parity` compares the
  VM with itself. The x86 → s390x check needs Part A run off-platform (Colab).

Caveats: single runs, shared VM, no warm-up control; x86 numbers come from a laptop with
other processes running. The two baselines are **not** a fair platform comparison and must
not be used as one.

### Real forecaster in the pipeline: full 2019, in process (BE-011, 2026-10-17)

`scripts/validate_replay_ingestion.py windows_2019.jsonl` on the VM (container `/opt/venv`,
`nice -n 10`, `SENTINEL_LOG_LEVEL=WARNING`), branch `zanetti/be-011-forecaster-wiring` @
`4d8b8f0`, `SENTINEL_FORECASTER=xgboost`, artefact `xgboost-33@0.1.0`, official registry,
in-memory audit, **mock detector**. Latency: last 10 000 samples (`total` includes the 144
`insufficient_history` windows, which skip the forecast).

| Run | Windows | Elapsed | `forecast` p50 / p99 ms | `total` p50 / p99 ms |
|---|---|---|---|---|
| Snap ML `auto` (→ NNPA), TreeSHAP off | 105 120 | 177.1 s | 1.371 / 2.042 | 1.574 / 2.387 |
| Snap ML `compress_trees`, TreeSHAP off | 105 120 | 98.8 s | **0.651** / 1.324 | **0.839** / 1.782 |
| Snap ML `auto`, TreeSHAP on (API default) | first 600 | 63.1 s | 135.98 / 175.37 | 134.39 / 168.71 |

All runs: 0 rejected, 0 processing issues (no `ForecastError`), 104 976 `scored`. The
forecast stage matches the ML owner's standalone numbers above (0.64 / 1.35 / 134 ms), so the
backend adds ≈ 0.2 ms per window around it.

**0 mock alerts over the whole year.** The mock detector uses σ = 1 m
(`SENTINEL_MOCK_DETECTOR_SIGMA_M`) and z ≥ 3 on ≥ 2 sensors; XGBoost residuals are millimetres
(σ < 0.02 m on 32 of 33 sensors, `docs/p2-forecasting.md`), so nothing reaches 3 m. This says
nothing about leak detection: the real detector with per-sensor σ from `meta.json` (BE-012,
P4) decides. Until then the shared API with XGBoost shows no alerts.

## CUSUM detector (x86-64, synthetic input) — 2026-10-17

Single measurements, **not a benchmark**: x86-64 laptop (Intel Core Ultra 7 155H, 22 threads,
other processes running), Python 3.14.7, `wip/be-012-docs` @ `a93883b`. They time tests, so they
include test set-up; they say nothing about the real models or about s390x.

| Command | Result |
|---|---|
| `uv run pytest tests/detection/test_end_to_end.py -q` | 14 passed in 3.33 s (5.6 s wall with `uv run` start-up) |
| `uv run pytest tests/detection/test_properties.py -q --durations=3` | 13 passed in 2.29 s; slowest: `test_null_series_run_length_matches_siegmund_arl0` 2.16 s |

The ARL0 test calls `evaluate` 100 000 times with one sensor and generates its noise inside the
timing: about 22 µs per window as an upper bound for the detector itself (2.16 s / 100 000).
The cost with 33 sensors through the pipeline and over HTTP is measured in the next two
sections. `just bench` / `scripts/bench_pipeline.py` build `Settings(audit_sink=...)` directly
and do not read `SENTINEL_DETECTOR`, so they still measure the mock detector.
Determinism digest of the fixed replay for BE-015: `7c5ef97bf8ccee4e19695475142658b426be29f796298314ab054a277ad3eee3` (x86-64; `d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413` before BE-012i, the same replay without the new `candidate_pipe` key, which passed on s390x on 2026-10-17), the
new literal is pending on s390x (`docs/testing.md`).

## CUSUM detector selected by `SENTINEL_DETECTOR=cusum` — 2026-10-17

Single measurements, **not benchmarks**: x86-64 laptop (Intel Core Ultra 7 155H, 22 threads,
other processes running), Linux 7.2.9, CPython 3.14.7, uvicorn 0.54.0, one worker. Synthetic
signal, invented calibration (`provenance = synthetic`, sigma about 0.07 m per sensor, `k = 0.5`,
`h = 10`, `N = 3`, `c = 2`, `h_close = 5`), persistence mock forecaster, placeholder registry
(33 pressure sensors), default history settings (capacity 288, 144 required), audit sink `null`
over HTTP and `memory` in process. They measure backend and detector overhead, not real models,
and say nothing about s390x.

### Over HTTP, real server (end-to-end run, `docs/testing.md`)

Source: the end-to-end report of the run on the tree of `79f2647` (scenario 1: ramp on 3 sensors,
one alert). Client: sequential, one new TCP connection per request, loopback; server numbers
from `GET /v1/status` after the replay.

| Measure | Value |
|---|---|
| windows | 330 sent, all `201` (144 `insufficient_history`, 186 scored) |
| wall time / client throughput | 1.082 s / 304.85 windows/s |
| client latency per `POST /v1/windows` p50 / p99 / max | 3.077 / 4.578 / 27.628 ms (the max is the very first request, cold path) |
| server `detection` stage p50 / p99 / max (186 scored windows) | 0.2947 / 1.0845 / 4.7047 ms |
| server `total` p50 / p99 (330 windows, including the 144 that skip forecast and detection) | 0.516 / 1.2946 ms |
| server `http_post_windows` p50 / p99 | 1.8118 / 2.8394 ms |

The other four replays of that run (330 windows each, fresh server each time) gave a client p50
between 2.856 and 2.923 ms and a p99 between 4.261 and 4.676 ms. One run per row, no warm-up and
no repetitions, so differences between runs are noise.

### In process, `build_pipeline` with the CUSUM selected from the environment

Command (script outside the repository: `scripts/bench_pipeline.py` with `Settings.from_env()`
instead of `Settings(audit_sink=...)`, same synthetic `generate(..., noise_m=0.05)` stream, no
leak, so no alert is raised):
`SENTINEL_DETECTOR=cusum SENTINEL_DETECTOR_CALIBRATION=<file> SENTINEL_AUDIT_SINK=memory python <script> 5000`.
5000 windows, 4856 scored. Three runs, then two runs of `uv run python scripts/bench_pipeline.py
--windows 5000` (mock detector) in the same session:

| Detector | `detection` p50 / p99 / max (ms) | `total` p50 / p99 (ms) |
|---|---|---|
| CUSUM, run 1 | 0.1101 / 0.3352 / 3.8771 | 0.1896 / 0.5821 |
| CUSUM, run 2 | 0.1054 / 0.3189 / 5.7599 | 0.1832 / 0.5408 |
| CUSUM, run 3 | 0.1142 / 0.3399 / 4.3838 | 0.1939 / 0.563 |
| mock, run 1 | 0.102 / 0.1747 / 2.7154 | 0.2288 / 0.402 |
| mock, run 2 | 0.0578 / 0.1328 / 3.176 | 0.1299 / 0.3037 |

The two sets of numbers are consistent (detection p50 about 0.1 to 0.3 ms with 33 sensors). The
mock rows differ from each other by almost a factor of two, so the machine noise is larger than
any CUSUM-versus-mock difference these runs could show. The cost per window is far below the
300 s interval; the CUSUM runs in plain Python and is not a measured bottleneck.

## Pipe network and candidate pipe (BE-012g/i, x86-64, CPython 3.14.7) — 2026-10-17

Single measurements, Intel Core Ultra 7 155H, the data owner's `l-town.json` (299 kB, 905
pipes, 785 nodes). Nothing here is on the per-window path: the candidate is computed only when an
alert opens.

| Operation | Time |
|---|---|
| `PipeNetwork.from_file` (median of 10 runs; range 3.1–6.9 ms) | 3.4 ms |
| `candidate_pipe` with 3 deviating sensors, first call (shortest paths not cached) / warm p50 over 200 calls | 1.5 ms / 0.67 ms |
| `candidate_pipe` with 16 deviating sensors, first call / warm p50 over 100 calls | 8.8 ms / 3.5 ms |
| 105 120 windows of 2018 through `CusumDetector.evaluate` (33 sensors, regions and network, 1 487 alerts each with a candidate) | 13.3 s and 16.1 s (two runs; about 0.13–0.15 ms per window) |

The last row is a scratch replay of the out-of-fold 2018 residuals of the development run; no 2019
file was read. The shortest paths are cached per sensor node on first use, so only the first alert
that involves a sensor pays the cold cost. Not measured on s390x.

## Offline tools of BE-012f (`src/sentinel/detection/offline/`, x86-64) — 2026-10-17

Single measurements of the development tools, same machine and Python as above (Intel Core
Ultra 7 155H, CPython 3.14.7, pandas from `uv.lock`), measured when the tools were written and
re-measured once for this document. Not part of the server. Timing only: the `z` matrices below
are invented noise with invented drops (`numpy.random.default_rng(0)`, 105 120 windows × 33
sensors, one year of 5-minute windows), parameters are arbitrary, and nothing is read from the
output.

| Operation | At implementation | Re-measured | Command / source |
|---|---|---|---|
| `replay` over 105 120 × 33, `k = 0.5`, `h = 8`, `c = 2`, `h_close = 4`, `N = 1` or `5` | 0.45–0.65 s | 0.469–0.765 s (5 runs for `N = 1`), 0.502–0.675 s (5 runs for `N = 5`) | `sentinel.detection.offline.sweep.replay`, timed with `time.perf_counter` |
| `sweep` of 18 configurations (`h` in 4, 6, 8, 12, 16, 24 × `N` in 1, 2, 3), 14 invented leaks | 8.9 s (about 0.5 s per configuration) | 9.35, 10.04, 11.22 s (3 runs; 0.52–0.62 s per configuration) | `sentinel.detection.offline.sweep.sweep` |
| `load_pressures_2018` (reads and hashes `scada_2018.csv.gz` of data v1, 105 120 × 33) | 0.30 s | 0.18–0.35 s (3 runs) | `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data` |
| `oof_hour_of_week_residuals(slot_minutes=60, statistic="median")` | 0.56 s | 0.43–0.59 s (3 runs) | same |
| `write_oof` (gzip CSV of the residuals + folds file) | 5.6 s | 4.90–6.24 s (3 runs) | same |

Only the 2018 file is opened by these tools (`load_pressures_2018` has no argument to open
another year). Each configuration of a `sweep` is a full replay, so its cost should grow
linearly with the number of configurations (18 configurations measured; larger grids not
measured).

## Calibration experiment of BE-012j/h on the XGBoost out-of-fold residuals (x86-64) — 2026-10-18

Same machine (Intel Core Ultra 7 155H, CPython 3.14.7). Real inputs: the ML owner's out-of-fold
residuals of both XGBoost variants (105 120 windows × 33 sensors each). Offline only.

| Operation | Measured | Command / source |
|---|---|---|
| `explore` (read and check the residual file, σ for 4 masks, Δ, φ, persistence, δ), per variant | 13.4 s and 13.7 s wall, peak RSS 1.10–1.14 GB | `/usr/bin/time python -m sentinel.detection.offline.experiment explore ...` |
| `sweep`, with neighbours | 458 s (first run), 259 s (second run, same results byte for byte) | `/usr/bin/time ... sweep ...`; peak RSS 1.10–1.15 GB |
| `sweep`, without neighbours | 231 s and 244 s | same |
| inside one sweep (with neighbours, separate timing): load + checks 3.1 s, phase A again 2.0 s, replay of the 95 main cells 69.1 s (76 076 openings), their evaluation 0.2 s, leave-one-leak-out for 4 budgets 2.6 s | single run | timing script with `time.perf_counter` (not committed) |

A sweep is about 326 replays (95 main cells, 190 for the `k` sensitivity, up to 36 for the cap
and release stage, 5 for the floors) at about 0.7 s each: the replay is the whole cost. The
458 s of the first run was not reproduced (259 s for the same work); it ran while other jobs
used the machine — not investigated.

## Observed bottlenecks / optimisation log

| # | Observation | Measurement | Hypothesis | Proposed change | Result |
|---|---|---|---|---|---|
| 1 | With mocks, HTTP handling dominates: server HTTP p50 3.96 ms vs pipeline p50 0.80 ms (x86); 0.85 vs 0.19 ms (s390x) | HTTP replays above | JSON parse + Pydantic validation of 37 readings + response serialisation, plus a new TCP connection per request in the stdlib client | keep-alive client for load tests (BE-017); no server change while it is far below budget | not applied |
| 2 | `temporal_policy` p50 0.05 ms, ~10× the `history` stage | bench above | dominated by `content_hash()` (canonical JSON + SHA-256) — **not profiled** | profile with `cProfile` before changing anything | — |
| 3 | `forecast` stage (mock) p50 0.07 ms includes building `ForecastRequest` with up to 288 windows | bench above | cost is tuple/model construction — **not profiled** | none needed now | — |

| 4 | Over HTTP, `validation` p50 0.0422 ms vs 0.0093 ms in process | full-year HTTP vs in-process runs above | `normalize_window` re-validates the whole window when turning `SensorWindowIn` into `SensorWindow` — **not profiled** | build the internal model without re-validating the readings (`model_construct` with the already validated fields) if it ever matters | not applied (0.03 ms vs a 300 s interval) |
| 5 | One spike: `detection` max 141.6 ms, client max 144 ms, during the full-year replay | full-year HTTP run | GC pause or contention with other members' kernels on 3 shared vCPUs — **not investigated** | none; watch p99 on real-model runs | — |
| 6 | TreeSHAP dominates the real forecaster: p50 134 ms per window vs 0.64 ms without it | `xgboost-33@0.1.0` on the VM, above | 33 `pred_contribs` calls on the native booster, one row each | `SENTINEL_FORECASTER_CONTRIBUTIONS=false` for long replays; keep contributions for the live demo | wired (BE-011); confirmed in the pipeline: 0.65 vs 136 ms per window; `on_alert` (default since `feat/p2-oof-residuals`): TreeSHAP only for the deviating sensors of an alert, after detection, so windows without an alert pay nothing — estimated ≈ 3.9 ms per explained sensor (127 ms / 33), **NOT_RUN on the VM** |
| 7 | Snap ML on the NNPA is slower than on CPU for this model (1.35 vs 0.64 ms per window; 5.4 vs 1.2 ms per 1000 rows) | same | transfer/setup cost dominates for small trees and few rows — **not profiled** | serve with `SENTINEL_FORECASTER_TREE_FORMAT=compress_trees` (full-year replay 98.8 s vs 177.1 s with `auto`), or keep `auto` to show the accelerator; test larger batches before deciding for bulk replays | option wired (BE-011); default still `auto` (ML owner's choice) |
| 8 | The calibration `sweep` takes 4–8 min per variant | section above: replay of the 95 main cells 69.1 s, leave-one-leak-out 2.6 s | one Python loop per window in `replay` (0.7 s per cell), cells replayed one after the other | replay the cells in parallel processes (independent), or derive the openings of every `N` and `h_close` from one `trajectory` per `(k, h, c)` | not applied (minutes, offline) |

No Rust/C++ optimisation is justified: there is no measured backend bottleneck relative to
the 300 s interval. Revisit only with LinuxONE measurements of the real models.

## Plan for the real evaluation (BE-015, BE-017)

1. On the LinuxONE VM (mock baseline done, see above): `just bench 105120` (one year of windows) with the real models →
   per-stage p50/p99, model share of total.
2. Full 2019 replay over HTTP with a keep-alive client → client p50/p99, server
   `http_post_windows` and `total` p50/p99, throughput, error rate from `/v1/status`.
3. Same commands on x86 for the byte-order/parity comparison (design doc §7).
4. Record command, commit, machine, versions and raw JSON output next to each number.
