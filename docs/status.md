# Project status — Backend & Ingestion

**Last update:** 2026-10-17 — the CUSUM detector (BE-012a..e) is selectable at startup (`SENTINEL_DETECTOR=cusum` + `SENTINEL_DETECTOR_CALIBRATION`, optional `SENTINEL_DETECTOR_REGIONS`) and was checked once against the real server with synthetic data; the offline tools of BE-012f (out-of-fold baseline residuals, replay/sweep, layer-1 counts) exist and are tested. Nothing is calibrated on real residuals yet: every CUSUM output is `is_mock=true`. Also on `main`: branch `feat/p2-forecasting` delivered the real forecaster (XGBoost per sensor, served with Snap ML) and measured it on the VM; wiring into the API (BE-011) is next.

## Summary

The backend runs end to end **with mocks**: windows are validated, ordered, buffered,
passed to a mock forecaster and a mock detector, alerts are stored and served, audit events
are emitted to a dev sink, and per-stage latency is measured. Nothing is integrated with the
real models or the real audit log yet. The real forecaster exists on `feat/p2-forecasting` (XGBoost, chosen by the ML owner; `docs/p2-forecasting.md`) and loads with `load_forecaster`, but the API still runs the mock until BE-011. The official sensor set is read from the data owner's
`config/sensors.battledim.json` (official registry, data owner), and the whole 2019 replay passes
validation with 0 rejections. On the LinuxONE VM (s390x) the
runtime installs from wheels and the 89 tests pass (recorded before the detector work); mypy does not install there yet.
`CusumDetector` (per-sensor episodes, gap decay, atomic rejection, calibration artefact with hash,
region ranking) exists and is tested with **invented, synthetic** calibrations. It is selected with
`SENTINEL_DETECTOR=cusum` and `SENTINEL_DETECTOR_CALIBRATION` (required; `SENTINEL_DETECTOR_REGIONS`
optional); the server refuses to start with a `ConfigError` on a missing or invalid file, another
forecaster, other or reordered sensors or a mapping that does not cover the registry, and `mock`
stays the default (README). A manual end-to-end run against the real `uvicorn` server with
synthetic data found no bug (`docs/testing.md`). Nothing is calibrated on real residuals
(DEP-DET-05) and any output of the CUSUM is `is_mock=true`. The offline tools of BE-012f live in
`src/sentinel/detection/offline/` (dev only, never imported by the server). All contracts are v1.0
`Proposed` until the other owners confirm them.

## Done (tests green)

BE-001 structure/tooling · BE-002 contracts + examples · BE-003 validation · BE-004 history ·
BE-005 ports + mocks · BE-006 pipeline · BE-007 HTTP API · BE-008 synthetic replay ·
BE-009 metrics/logs/benchmark (x86, mocks) · BE-010 real sensor registry · BE-018 CI (GitHub Actions) · BE-019 fail-closed audit + z sign
convention · BE-014 full 2019 HTTP replay (mocks) · BE-020 shared API script + runbook ·
BE-012a..e CUSUM detector: episodes, gaps, atomic rejection, calibration artefact, `calibrate()`,
`for_pipeline`, region ranking · BE-012 selection `SENTINEL_DETECTOR=cusum` in `build_pipeline`
(startup refusals as `ConfigError`, `mock` default; `tests/test_detector_startup.py`) ·
BE-012f parts 1 and 2: out-of-fold hour-of-week residuals (`offline/baseline.py`), replay with
exact parity with the detector and parameter sweep (`offline/sweep.py`), layer-1 counts with a
**provisional** matching rule (`offline/layer1.py`, Q-UI-06). Details:
`docs/implementation-plan.md`.

## In progress

- BE-012 umbrella: selection and tools done; the final calibration and the layer-1 evaluation
  (BE-012h) are blocked.
- BE-012f: development calibration on 2018 (out-of-fold hour-of-week baseline,
  `provenance = dev-baseline-2018`, `is_mock = true`). Parts 1 and 2 (the tools) are done; the
  development run that produces the artefact, the sensitivity of σ and the delay × false-alarms
  numbers is in progress. Unblocked (BE-010 on `main`, data v1 available locally).
- BE-015 LinuxONE: mock baseline, parity hash and HTTP replay done; full `just check`
  waits for Q-PLAT-02 (`gcc`), model measurements wait for BE-011/012. The detector
  determinism digest `d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413` (x86-64, `tests/detection/test_determinism.py`) is **pending on s390x**.

## Blocked

| Task | Waiting for |
|---|---|
| BE-012g real L-Town sensor → region mapping | DEP-DET-04, Q-DET-05 (detection owner) |
| BE-012h final calibration and layer-1 evaluation | BE-012f/g, BE-011, DEP-DET-05, Q-ML-07/08 (ML owner), Q-TEAM-03 |
| BE-013 real audit sink | DEP-AUD-01 (audit owner) |

## Pending (independent, not started)

BE-011 wire the real forecaster (`SENTINEL_FORECASTER=xgboost` + artefact path; unblocked by `feat/p2-forecasting`) · BE-016 dashboard needs (after Q-UI answers) · BE-017 load test with keep-alive client.

Small leftovers of the CUSUM selection, outside the scope of the documentation pass that
recorded them: the comment above `SENTINEL_FORECASTER` in `.env.example` still says that only
`mock` exists and the file does not list the new detector variables; `scripts/bench_pipeline.py`
builds its `Settings` without the environment, so `just bench` cannot measure the CUSUM;
`scripts/shared_api.sh` does not forward `SENTINEL_DETECTOR*`; a refused startup prints a Python
traceback whose last line is the `ConfigError` (for pydantic errors, a documentation URL).

P6 dashboard & evaluation: UI-001 DONE (dashboard tracer), UI-002 DONE (playback + fixtures), UI-003..006 DONE (recent alerts, detail, polling + history, cross-team proposals + ADR-009); UI-007 TODO; EV-001 IN_PROGRESS (ADR-010 `Proposed`, awaiting P4 review); EV-002..003 TODO, spec and tickets in
`.scratch/p6-dashboard-evaluation/`; EV-003 gated on the real detector by 2026-10-17 22:00.

## External dependencies

17 open/partial items in `docs/dependencies.md` (new: DEP-DET-05, out-of-fold 2018 residuals from
the ML owner; DEP-DET-04 is now partial); P0 questions in
`docs/questions-for-team.md` (Q-TEAM-01/02, Q-ML-01/02/08, Q-DET-01,
Q-AUD-01/03); Q-PLAT-01 answered, Q-PLAT-02 (P1) open.

## Risks

- Unmerged owner branches change the P6 plan (2026-10-17 18:30): `feat/be-012-cusum-detector`
  (real CUSUM, ADR-007/008: layer-2 2019 evaluation through the API is P6's, Q-UI-06) and
  `leo/local-version` (hash chain, verifier CLI, narrative; no HTTP endpoint, records fail-open,
  Q-DOC-01). P6 renumbered to ADR-009/010, Q-UI-07, Q-DET-07.

- The shared API on port 8000 holds 2019 in memory; any restart wipes it (re-run the replay,
  `docs/runbook-vm.md`).
- Separate repository may need to be merged into a team repository (Q-TEAM-02).
- Gap policy blinds scoring for 12 h after any missing window (ADR-004); v1 has no gaps.
- Fail-closed audit: during an audit outage no new alerts are served; the pending queue is in
  memory and lost on restart (ADR-006).
- In-memory state: restart loses history, results, alerts, metrics, detector state (ADR-003).
- Detector: the calibration numbers exist only in tests and are invented; nothing is calibrated
  on real residuals yet. The false-alarm count of a sweep is not monotone in `h` (ADR-008 §E), so
  a sweep table must not be interpolated, and the layer-1 matching rule is provisional (Q-UI-06):
  any delay or false-alarm count computed with it is provisional too. With the persistence mock
  forecaster a step does not accumulate (a ramp does), so the served CUSUM is only a mechanics
  check until the real forecaster arrives (ADR-007 §1). A new CUSUM episode on a sensor whose
  `S` has not returned to 0 inherits the old onset (Q-DET-06). If the pipeline discards a result
  the detector already returned, its state has advanced while the window was lost (ADR-007
  Risks).
- `mypy` not installable on s390x without `gcc` (Q-PLAT-02); model runtime wheels on s390x unverified. Model runtime (numpy, xgboost, snapml) comes from the VM image's `/opt/venv`, not `uv`; it is lost when the container is recreated (DEP-ML-03).
- With TreeSHAP contributions the forecaster takes ~134 ms per window: ≈ 3.9 h for a full-year replay (estimate; ≈ 67 s without contributions, `docs/performance.md`).

## Known bottlenecks

With mocks on x86, HTTP handling (p50 ≈ 4 ms server-side) dominates the pipeline
(p50 ≈ 0.4–0.8 ms). Both are far below the 300 s interval. With the CUSUM selected (synthetic
calibration, mock forecaster, x86) the `detection` stage had p50 about 0.1 ms in process and
0.29 ms over HTTP, single measurements (`docs/performance.md`).

## Future optimisations

Only with evidence from LinuxONE + real models: profile `content_hash`, keep-alive replay
client, out-of-process model adapter if a runtime cannot be imported (ADR-005).

## Pending for the detection owner

- Finish BE-012f (the development run).
- Pre-registration of ADR-008 §G, all `not yet registered`: δ (→ `k`), persistence-test
  criterion, false-alarm budget (Q-TEAM-03), the region rule (Q-DET-05), artefact hash. Must be
  committed before any evaluation run.
- Open questions: Q-ML-07/08 (ML), Q-UI-05/06 (dashboard/evaluation), Q-TEAM-03, Q-DET-05,
  Q-DET-06 (onset inherited by a new episode). Dependencies DEP-DET-04/05.
- ADR-007/008 are `Proposed`: they need the ML owner (Q-ML-07), the dashboard owner (Q-UI-05)
  and the evaluation owner (Q-UI-06).

## Next recommended step

Finish BE-012f (the development run on the 2018 baseline residuals), register the ADR-008 §G
fields, then wire BE-011 (`load_forecaster("xgboost", <path>)` at startup, with an option to turn
TreeSHAP off for long replays) and the real audit sink as they arrive (BE-011/013) on the shared
API (`docs/runbook-vm.md`; the script currently starts the mock detector) and measure p50/p99
with real models (BE-015, incl. the s390x run of the determinism test). Independent: BE-017
(keep-alive load test).

## Verification (2026-10-17)

| Command | Result |
|---|---|
| Merge of `main` (a63d367: CUSUM detector, XGBoost forecaster) into `feat/dashboard-mvp`; conflict markers left staged in `CHANGELOG.md`, `docs/adr/README.md`, `docs/dependencies.md` resolved | ruff OK · mypy: no issues in 112 files · **599 passed, 18 skipped** |
| Dashboard vs real CUSUM: `fixture-cusum` (`scripts/make_playback_fixtures.py`) + `tests/test_dashboard_cusum.py`; gates | mypy: no issues in 113 files · **604 passed, 18 skipped** |
| Scratch e2e (not committed): real `uvicorn` with `SENTINEL_DETECTOR=cusum` (invented synthetic calibration + 2-region mapping), 288 synthetic windows with a ramp on n1/n4/n31, dashboard `AppTest` over HTTP | 288 × 201; 1 alert `A-20190314T1705Z-1`, region R-SYN-A, onset 16:35, CUSUM 9.393 > 8.0, age 6 h 50 min, audit seq 205; SIMULATED shown; no exception |
| UI-004..006 integrated (three worktrees, cherry-picked; app.py conflict resolved) + review fixes: `ruff format --check`, `ruff check`, `mypy`, `pytest` | ruff OK · mypy: no issues in 81 files · **225 passed, 8 skipped** (P1 data tests) |
| UI-003 gates (x86, Python 3.12): `ruff format`, `ruff check`, `mypy`, `pytest` | ruff OK · mypy: no issues in 74 files · **169 passed, 8 skipped** (P1 data tests) |
| UI-002 gates (x86, Python 3.12): `ruff format`, `ruff check`, `mypy`, `pytest` | ruff OK · mypy: no issues in 72 files · **160 passed, 8 skipped** (P1 data tests) |
| UI-001 gates (x86, Python 3.12, `uv sync --locked --group dashboard`): `ruff format`, `ruff check`, `mypy`, `pytest` over src tests scripts dashboard | ruff OK · mypy: no issues in 70 files · **133 passed, 8 skipped** (P1 data tests, no `$SENTINEL_DATA_DIR`) |
| UI-001 smoke: SSH tunnel `-L 8000:172.17.0.2:8000 linux1@148.100.99.103`, dashboard `AppTest` + `streamlit run --server.headless` | header: API online, simulated time 2019-12-31 23:55 UTC, mock-persistence/mock-threshold, SIMULATED badge; `/_stcore/health` ok |
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
| branch `wip/be-012-docs` @ `a93883b` — x86-64 (Intel Core Ultra 7 155H), Python 3.14.7: `just check` | ruff format: 75 files unchanged · ruff check OK · mypy: no issues in 75 source files · **299 passed, 8 skipped** (the 8 skips are the P1 data tests without `SENTINEL_DATA_DIR`) |
| same, `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data uv run pytest tests/test_p1_data.py -q` | **14 passed** |
| same, `uv run pytest tests/detection -q` | **178 passed** |
| detector determinism digest, x86-64 (`tests/detection/test_determinism.py`) | `d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413` · s390x: **NOT_RUN** (BE-015) |
| branch `feat/p2-forecasting` @ `17e71af`+ — x86 `ruff format --check`, `ruff check`, `mypy`, `pytest` (`.venv`, no ML libraries) | 74 files formatted · ruff OK · mypy OK (74 files) · **152 passed, 17 skipped** (ML tests skip) |
| same, x86 with numpy/xgboost/snapml/tensorflow/torch/onnx (`PYTHONPATH=src pytest`) | **161 passed, 8 skipped** (P1 data tests) |
| LinuxONE `python -m pytest -q` in the ML owner's clone @ `1278294` (older commit of the branch) | **166 passed**; the latest commits are **NOT_RUN** on s390x |
| LinuxONE notebooks 01–03 on the real `data/v1` (XGBoost: 600-tree run; GRU run mode not recorded), metrics v2 | val MAE on clean windows baseline 0.105 / XGBoost 0.006 / GRU 0.077 m · leak contrast 4.16 / 3.39 / 1.80 · p50 per window 0.025 / 134.5 (with TreeSHAP) / 10.2 ms |
| real models in the API | **NOT_RUN** — BE-011 not wired |
| line coverage | **NOT_RUN** — no coverage tool configured |
| branch `wip/be-012-docs-final`, base `56170f9` plus this documentation change — x86-64 (Intel Core Ultra 7 155H), Python 3.14.7: `just check` (ruff format, ruff check, mypy strict, pytest) | ruff format: 83 files left unchanged · ruff check: all checks passed · mypy: no issues found in 83 source files · **464 passed, 9 skipped** (8 P1 data tests and 1 offline-baseline real-data test, skipped without `SENTINEL_DATA_DIR`) |
| same, `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data uv run pytest tests/test_p1_data.py tests/detection/test_offline_baseline.py -q` | **48 passed** (the real-data tests ran; the offline one checks shape and finiteness only) |
| same, `uv run pytest tests/detection -q` (no `SENTINEL_DATA_DIR`) | **312 passed, 1 skipped** |
| same, `uv run pytest tests/test_detector_startup.py tests/test_config.py -q` | **39 passed** |
| README procedure with a synthetic calibration and regions (uvicorn on 127.0.0.1, `SENTINEL_DETECTOR=cusum`) | `/health` ok; `/v1/status` reports detector `cusum` `0.1.0+<8 hex>`, `is_mock: true`, `mocks_active: true`; `cusum` without the calibration variable, a missing calibration file and the calibration variable set with the mock detector each end in a `ConfigError` |
| manual end-to-end run on the real server, tree of `79f2647`, synthetic data (`docs/testing.md`) | 84 checks passed, no bug found; 330 windows over HTTP in 1.082 s, client p50 3.077 / p99 4.578 ms (`docs/performance.md`) |
| `SENTINEL_DETECTOR=cusum` on the s390x VM, the shared API | **NOT_RUN** — the shared API runs the mock detector (`scripts/shared_api.sh` does not forward `SENTINEL_DETECTOR*`) |
