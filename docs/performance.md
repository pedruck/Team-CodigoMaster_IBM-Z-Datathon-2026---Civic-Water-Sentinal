# Performance

Goal from the design document (§7): p50/p99 latency per window **measured on LinuxONE**.
Nothing below was measured on LinuxONE yet. All numbers so far use the **mock**
forecaster/detector, so they measure backend overhead only, not model inference.

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

## Observed bottlenecks / optimisation log

| # | Observation | Measurement | Hypothesis | Proposed change | Result |
|---|---|---|---|---|---|
| 1 | With mocks, HTTP handling dominates: server HTTP p50 3.96 ms vs pipeline p50 0.80 ms; client p50 6.8 ms | HTTP replay above | JSON parse + Pydantic validation of 37 readings + response serialisation, plus a new TCP connection per request in the stdlib client | keep-alive client for load tests (BE-017); no server change while it is far below budget | not applied |
| 2 | `temporal_policy` p50 0.05 ms, ~10× the `history` stage | bench above | dominated by `content_hash()` (canonical JSON + SHA-256) — **not profiled** | profile with `cProfile` before changing anything | — |
| 3 | `forecast` stage (mock) p50 0.07 ms includes building `ForecastRequest` with up to 288 windows | bench above | cost is tuple/model construction — **not profiled** | none needed now | — |

No Rust/C++ optimisation is justified: there is no measured backend bottleneck relative to
the 300 s interval. Revisit only with LinuxONE measurements of the real models.

## Plan for the real evaluation (BE-015, BE-017)

1. On the LinuxONE VM: `just bench 105120` (one year of windows) with the real models →
   per-stage p50/p99, model share of total.
2. Full 2019 replay over HTTP with a keep-alive client → client p50/p99, server
   `http_post_windows` and `total` p50/p99, throughput, error rate from `/v1/status`.
3. Same commands on x86 for the byte-order/parity comparison (design doc §7).
4. Record command, commit, machine, versions and raw JSON output next to each number.
