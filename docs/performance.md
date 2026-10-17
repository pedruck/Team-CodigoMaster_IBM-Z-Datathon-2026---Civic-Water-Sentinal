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
(DEP-ML-03).

Caveats: single runs, shared VM, no warm-up control; x86 numbers come from a laptop with
other processes running. The two baselines are **not** a fair platform comparison and must
not be used as one.

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
Determinism digest of the fixed replay for BE-015: `d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413` (x86-64), s390x pending
(`docs/testing.md`).

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

## Observed bottlenecks / optimisation log

| # | Observation | Measurement | Hypothesis | Proposed change | Result |
|---|---|---|---|---|---|
| 1 | With mocks, HTTP handling dominates: server HTTP p50 3.96 ms vs pipeline p50 0.80 ms (x86); 0.85 vs 0.19 ms (s390x) | HTTP replays above | JSON parse + Pydantic validation of 37 readings + response serialisation, plus a new TCP connection per request in the stdlib client | keep-alive client for load tests (BE-017); no server change while it is far below budget | not applied |
| 2 | `temporal_policy` p50 0.05 ms, ~10× the `history` stage | bench above | dominated by `content_hash()` (canonical JSON + SHA-256) — **not profiled** | profile with `cProfile` before changing anything | — |
| 3 | `forecast` stage (mock) p50 0.07 ms includes building `ForecastRequest` with up to 288 windows | bench above | cost is tuple/model construction — **not profiled** | none needed now | — |

| 4 | Over HTTP, `validation` p50 0.0422 ms vs 0.0093 ms in process | full-year HTTP vs in-process runs above | `normalize_window` re-validates the whole window when turning `SensorWindowIn` into `SensorWindow` — **not profiled** | build the internal model without re-validating the readings (`model_construct` with the already validated fields) if it ever matters | not applied (0.03 ms vs a 300 s interval) |
| 5 | One spike: `detection` max 141.6 ms, client max 144 ms, during the full-year replay | full-year HTTP run | GC pause or contention with other members' kernels on 3 shared vCPUs — **not investigated** | none; watch p99 on real-model runs | — |

No Rust/C++ optimisation is justified: there is no measured backend bottleneck relative to
the 300 s interval. Revisit only with LinuxONE measurements of the real models.

## Plan for the real evaluation (BE-015, BE-017)

1. On the LinuxONE VM (mock baseline done, see above): `just bench 105120` (one year of windows) with the real models →
   per-stage p50/p99, model share of total.
2. Full 2019 replay over HTTP with a keep-alive client → client p50/p99, server
   `http_post_windows` and `total` p50/p99, throughput, error rate from `/v1/status`.
3. Same commands on x86 for the byte-order/parity comparison (design doc §7).
4. Record command, commit, machine, versions and raw JSON output next to each number.
