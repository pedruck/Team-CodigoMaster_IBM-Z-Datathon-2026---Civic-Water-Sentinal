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

## Observed bottlenecks / optimisation log

| # | Observation | Measurement | Hypothesis | Proposed change | Result |
|---|---|---|---|---|---|
| 1 | With mocks, HTTP handling dominates: server HTTP p50 3.96 ms vs pipeline p50 0.80 ms (x86); 0.85 vs 0.19 ms (s390x) | HTTP replays above | JSON parse + Pydantic validation of 37 readings + response serialisation, plus a new TCP connection per request in the stdlib client | keep-alive client for load tests (BE-017); no server change while it is far below budget | not applied |
| 2 | `temporal_policy` p50 0.05 ms, ~10× the `history` stage | bench above | dominated by `content_hash()` (canonical JSON + SHA-256) — **not profiled** | profile with `cProfile` before changing anything | — |
| 3 | `forecast` stage (mock) p50 0.07 ms includes building `ForecastRequest` with up to 288 windows | bench above | cost is tuple/model construction — **not profiled** | none needed now | — |

| 4 | Over HTTP, `validation` p50 0.0422 ms vs 0.0093 ms in process | full-year HTTP vs in-process runs above | `normalize_window` re-validates the whole window when turning `SensorWindowIn` into `SensorWindow` — **not profiled** | build the internal model without re-validating the readings (`model_construct` with the already validated fields) if it ever matters | not applied (0.03 ms vs a 300 s interval) |
| 5 | One spike: `detection` max 141.6 ms, client max 144 ms, during the full-year replay | full-year HTTP run | GC pause or contention with other members' kernels on 3 shared vCPUs — **not investigated** | none; watch p99 on real-model runs | — |
| 6 | TreeSHAP dominates the real forecaster: p50 134 ms per window vs 0.64 ms without it | `xgboost-33@0.1.0` on the VM, above | 33 `pred_contribs` calls on the native booster, one row each | `load_forecaster(..., with_contributions=False)` for long replays; keep contributions for the live demo (BE-011 decides how to expose it) | option added on `feat/p2-forecasting`; not wired |
| 7 | Snap ML on the NNPA is slower than on CPU for this model (1.35 vs 0.64 ms per window; 5.4 vs 1.2 ms per 1000 rows) | same | transfer/setup cost dominates for small trees and few rows — **not profiled** | serve with `tree_format="compress_trees"`, or keep `auto` to show the accelerator; test larger batches before deciding for bulk replays | open (ML owner) |

No Rust/C++ optimisation is justified: there is no measured backend bottleneck relative to
the 300 s interval. Revisit only with LinuxONE measurements of the real models.

## Plan for the real evaluation (BE-015, BE-017)

1. On the LinuxONE VM (mock baseline done, see above): `just bench 105120` (one year of windows) with the real models →
   per-stage p50/p99, model share of total.
2. Full 2019 replay over HTTP with a keep-alive client → client p50/p99, server
   `http_post_windows` and `total` p50/p99, throughput, error rate from `/v1/status`.
3. Same commands on x86 for the byte-order/parity comparison (design doc §7).
4. Record command, commit, machine, versions and raw JSON output next to each number.
