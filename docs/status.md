# Project status — Backend & Ingestion

**Last update:** 2026-10-17 — branch `feat/p2-forecasting`: real forecaster delivered (XGBoost per sensor, served with Snap ML) and measured on the VM; wiring into the API (BE-011) is next. Before: branch `zanetti/shared-api` (one sensor-ID scheme, shared API script + runbook, full 2019 HTTP replay).

## Summary

The backend runs end to end **with mocks**: windows are validated, ordered, buffered,
passed to a mock forecaster and a mock detector, alerts are stored and served, audit events
are emitted to a dev sink, and per-stage latency is measured. Nothing is integrated with the
real models or the real audit log yet. The real forecaster exists on `feat/p2-forecasting` (XGBoost, chosen by the ML owner; `docs/p2-forecasting.md`) and loads with `load_forecaster`, but the API still runs the mock until BE-011. The official sensor set is read from the data owner's
`config/sensors.battledim.json` (official registry, data owner), and the whole 2019 replay passes
validation with 0 rejections. On the LinuxONE VM (s390x) the
runtime installs from wheels and the 89 tests pass; mypy does not install there yet. All contracts are v1.0
`Proposed` until the other owners confirm them.

## Done (tests green)

BE-001 structure/tooling · BE-002 contracts + examples · BE-003 validation · BE-004 history ·
BE-005 ports + mocks · BE-006 pipeline · BE-007 HTTP API · BE-008 synthetic replay ·
BE-009 metrics/logs/benchmark (x86, mocks) · BE-010 real sensor registry · BE-018 CI (GitHub Actions) · BE-019 fail-closed audit + z sign
convention · BE-014 full 2019 HTTP replay (mocks) · BE-020 shared API script + runbook. Details: `docs/implementation-plan.md`.

## In progress

BE-015 LinuxONE: mock baseline, parity hash and HTTP replay done; full `just check`
waits for Q-PLAT-02 (`gcc`), model measurements wait for BE-011/012.

## Blocked

| Task | Waiting for |
|---|---|
| BE-012 real detector | DEP-DET-01..03 (detection owner) |
| BE-013 real audit sink | DEP-AUD-01 (audit owner) |

## Pending (independent, not started)

BE-011 wire the real forecaster (`SENTINEL_FORECASTER=xgboost` + artefact path; unblocked by `feat/p2-forecasting`) · BE-016 dashboard needs (after Q-UI answers) · BE-017 load test with keep-alive client.

## External dependencies

22 open/partial items in `docs/dependencies.md`; P0 questions in
`docs/questions-for-team.md` (Q-TEAM-01/02, Q-ML-01/02, Q-DET-01/02,
Q-AUD-01/03); Q-PLAT-01 answered, Q-PLAT-02 (P1) open.

## Risks

- The shared API on port 8000 holds 2019 in memory; any restart wipes it (re-run the replay,
  `docs/runbook-vm.md`).
- Separate repository may need to be merged into a team repository (Q-TEAM-02).
- Gap policy blinds scoring for 12 h after any missing window (ADR-004); v1 has no gaps.
- Fail-closed audit: during an audit outage no new alerts are served; the pending queue is in
  memory and lost on restart (ADR-006).
- In-memory state: restart loses history, results, alerts, metrics, detector state (ADR-003).
- `mypy` not installable on s390x without `gcc` (Q-PLAT-02). Model runtime (numpy, xgboost, snapml) comes from the VM image's `/opt/venv`, not `uv`; it is lost when the container is recreated (DEP-ML-03).
- With TreeSHAP contributions the forecaster takes ~134 ms per window: ≈ 3.9 h for a full-year replay (estimate; ≈ 67 s without contributions, `docs/performance.md`).

## Known bottlenecks

With mocks on x86, HTTP handling (p50 ≈ 4 ms server-side) dominates the pipeline
(p50 ≈ 0.4–0.8 ms). Both are far below the 300 s interval. See `docs/performance.md`.

## Future optimisations

Only with evidence from LinuxONE + real models: profile `content_hash`, keep-alive replay
client, out-of-process model adapter if a runtime cannot be imported (ADR-005).

## Next recommended step

BE-011: wire `load_forecaster("xgboost", <path>)` at startup (with an option to turn TreeSHAP off for long replays), then the real detector/audit as they arrive (BE-012/013) on the shared API
(`docs/runbook-vm.md`), then measure p50/p99 with real models (BE-015). Independent: BE-017.

## Verification (2026-10-17)

| Command | Result |
|---|---|
| `just check` (ruff format, ruff check, mypy strict, pytest) | 52 files unchanged · ruff: all checks passed · mypy: no issues in 52 files · **89 passed** |
| `just bench 5000` | 5000 windows, total p50 0.3707 ms / p99 0.7358 ms (mocks) |
| uvicorn + `scripts/replay.py` (576 synthetic windows) | 576 × 201; resend 5 × 200; 1 mock alert; client p50 6.797 / p99 14.837 ms |
| `just dev` + README curl + `just replay` (fresh server) | example → 201 `insufficient_history`; 288 × 201; 1 mock alert |
| GitHub Actions run 39040317019 (`just ci`, ubuntu-latest, Python 3.12) | success · 52 files already formatted · ruff OK · mypy OK · **89 passed** |
| LinuxONE VM (s390x, Python 3.12.3): `uv sync --locked --no-dev` + pinned `ruff format --check`, `ruff check`, `pytest` | runtime deps installed from wheels · 52 files formatted · ruff OK · **89 passed** |
| LinuxONE: `uv sync --locked` (with dev group) | **FAILED** — `librt` 0.16.0 (mypy dep) needs `gcc`, absent on the VM |
| LinuxONE: `scripts/bench_pipeline.py --windows 5000` | total p50 0.1645 ms / p99 0.3514 ms (mocks) |
| LinuxONE: uvicorn + `scripts/replay.py` (576 windows, localhost) | 576 × 201; resend 5 × 200; 1 mock alert; client p50 1.419 / p99 17.988 ms |
| x86 vs s390x `content_hash` of `window.valid.json` | identical (`7fc91e5a…9717fa`) |
| LinuxONE container (`/opt/venv`), branch `zanetti/ingestion` @ `073c605`: `python -m pytest -q` | **107 passed** |
| x86: `just check` @ `073c605` | ruff OK · mypy OK (57 files) · **107 passed** |
| LinuxONE: `scripts/validate_v1_ingestion.py $SENTINEL_DATA_DIR/v1 --year 2019` @ `073c605` (CSV timestamps sent as is) | 105 120 windows, 0 rejected, 105 120 with `timestamp_tz_assumed`, pipeline p50 0.2231 / p99 0.4074 ms |
| branch `zanetti/audit-fail-closed` @ `1251d95` — x86 `just ci` · LinuxONE `python -m pytest -q` · LinuxONE `validate_v1_ingestion.py` 2019 (in-memory audit) | ruff OK · mypy OK (58 files) · **115 passed** (x86 and s390x) · 105 120 windows, 0 rejected, p50 0.221 / p99 0.3156 ms |
| branch `zanetti/shared-api` @ `d4a2750` — x86 `just check` · LinuxONE `python -m pytest -q` (with `SENTINEL_DATA_DIR`) | ruff OK · mypy OK (63 files) · x86 121 passed + 8 skipped (P1 data) · **s390x 129 passed** |
| LinuxONE: `validate_replay_ingestion.py windows_2019.jsonl` (in process) | 105 120 windows, 0 rejected, 0 tz assumed, p50 0.2157 / p99 0.3026 ms |
| LinuxONE: `shared_api.sh start / start (dup) / stop / status / start` | started; duplicate refused (exit 1); stopped, process gone; restarted |
| LinuxONE: `scripts/replay.py windows_2019.jsonl` → shared API :8000 | 105 120 × 201, 723.78 windows/s, client p50 1.284 / p99 2.861 ms |
| local tunnel `-L 18000:172.17.0.2:8000` | `/health` ok, `/docs` 200 |
| branch `feat/p2-forecasting` @ `17e71af`+ — x86 `ruff format --check`, `ruff check`, `mypy`, `pytest` (`.venv`, no ML libraries) | 74 files formatted · ruff OK · mypy OK (74 files) · **152 passed, 17 skipped** (ML tests skip) |
| same, x86 with numpy/xgboost/snapml/tensorflow/torch/onnx (`PYTHONPATH=src pytest`) | **161 passed, 8 skipped** (P1 data tests) |
| LinuxONE `python -m pytest -q` in the ML owner's clone @ `1278294` (older commit of the branch) | **166 passed**; the latest commits are **NOT_RUN** on s390x |
| LinuxONE notebooks 01–03 on the real `data/v1` (XGBoost: 600-tree run; GRU run mode not recorded), metrics v2 | val MAE on clean windows baseline 0.105 / XGBoost 0.006 / GRU 0.077 m · leak contrast 4.16 / 3.39 / 1.80 · p50 per window 0.025 / 134.5 (with TreeSHAP) / 10.2 ms |
| real models in the API | **NOT_RUN** — BE-011 not wired |
| line coverage | **NOT_RUN** — no coverage tool configured |
