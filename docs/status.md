# Project status — Backend & Ingestion

**Last update:** 2026-10-17 — CUSUM detector (BE-012a..e) implemented and tested behind the `Detector` port; detector design documented (ADR-007/008, contract §3 semantics). `SENTINEL_DETECTOR=cusum` wiring and the development calibration (BE-012f) are in progress.

## Summary

The backend runs end to end **with mocks**: windows are validated, ordered, buffered,
passed to a mock forecaster and a mock detector, alerts are stored and served, audit events
are emitted to a dev sink, and per-stage latency is measured. Nothing is integrated with the
real models or the real audit log yet. The official sensor set is read from the data owner's
`config/sensors.battledim.json` (official registry, data owner), and the whole 2019 replay passes
validation with 0 rejections. On the LinuxONE VM (s390x) the
runtime installs from wheels and the 89 tests pass (recorded before the detector work); mypy does not install there yet.
`CusumDetector` (per-sensor episodes, gap decay, atomic rejection, calibration artefact with hash,
region ranking) exists and is tested with **invented, synthetic** calibrations: it is not selectable
at startup yet (`SENTINEL_DETECTOR=cusum` in progress), nothing is calibrated on real residuals
(DEP-DET-05) and any output of it is `is_mock=true`. All contracts are v1.0 `Proposed` until the
other owners confirm them.

## Done (tests green)

BE-001 structure/tooling · BE-002 contracts + examples · BE-003 validation · BE-004 history ·
BE-005 ports + mocks · BE-006 pipeline · BE-007 HTTP API · BE-008 synthetic replay ·
BE-009 metrics/logs/benchmark (x86, mocks) · BE-010 real sensor registry · BE-018 CI (GitHub Actions) · BE-019 fail-closed audit + z sign
convention · BE-014 full 2019 HTTP replay (mocks) · BE-020 shared API script + runbook ·
BE-012a..e CUSUM detector: episodes, gaps, atomic rejection, calibration artefact, `calibrate()`,
`for_pipeline`, region ranking (injectable through `build_pipeline`, not yet selectable by
environment variable). Details: `docs/implementation-plan.md`.

## In progress

- BE-012 umbrella: wiring `SENTINEL_DETECTOR=cusum` in `config.py` / `main.py`.
- BE-012f: development calibration on 2018 (out-of-fold hour-of-week baseline,
  `provenance = dev-baseline-2018`, `is_mock = true`) in `src/sentinel/detection/offline/`;
  unblocked (BE-010 on `main`, data v1 available locally).
- BE-015 LinuxONE: mock baseline, parity hash and HTTP replay done; full `just check`
  waits for Q-PLAT-02 (`gcc`), model measurements wait for BE-011/012. The detector
  determinism digest `d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413` (x86-64, `tests/detection/test_determinism.py`) is **pending on s390x**.

## Blocked

| Task | Waiting for |
|---|---|
| BE-011 real forecaster | DEP-ML-01..03 (ML owner) |
| BE-012g real L-Town sensor → region mapping | DEP-DET-04, Q-DET-05 (detection owner) |
| BE-012h final calibration and layer-1 evaluation | BE-012f/g, BE-011, DEP-DET-05, Q-ML-07/08 (ML owner), Q-TEAM-03 |
| BE-013 real audit sink | DEP-AUD-01 (audit owner) |

## Pending (independent, not started)

BE-016 dashboard needs (after Q-UI answers) · BE-017 load test with keep-alive client.

## External dependencies

17 open/partial items in `docs/dependencies.md` (new: DEP-DET-05, out-of-fold 2018 residuals from
the ML owner; DEP-DET-04 is now partial); P0 questions in
`docs/questions-for-team.md` (Q-TEAM-01/02, Q-ML-01/02/08, Q-DET-01,
Q-AUD-01/03); Q-PLAT-01 answered, Q-PLAT-02 (P1) open.

## Risks

- The shared API on port 8000 holds 2019 in memory; any restart wipes it (re-run the replay,
  `docs/runbook-vm.md`).
- Separate repository may need to be merged into a team repository (Q-TEAM-02).
- Gap policy blinds scoring for 12 h after any missing window (ADR-004); v1 has no gaps.
- Fail-closed audit: during an audit outage no new alerts are served; the pending queue is in
  memory and lost on restart (ADR-006).
- In-memory state: restart loses history, results, alerts, metrics, detector state (ADR-003).
- Detector: the calibration numbers exist only in tests and are invented; nothing is calibrated
  on real residuals yet. A new CUSUM episode on a sensor whose `S` has not returned to 0 inherits
  the old onset (Q-DET-06). If the pipeline discards a result the detector already returned, its
  state has advanced while the window was lost (ADR-007 Risks).
- `mypy` not installable on s390x without `gcc` (Q-PLAT-02); model runtime wheels on s390x unverified.

## Known bottlenecks

With mocks on x86, HTTP handling (p50 ≈ 4 ms server-side) dominates the pipeline
(p50 ≈ 0.4–0.8 ms). Both are far below the 300 s interval. See `docs/performance.md`.

## Future optimisations

Only with evidence from LinuxONE + real models: profile `content_hash`, keep-alive replay
client, out-of-process model adapter if a runtime cannot be imported (ADR-005).

## Pending for the detection owner

- Wire `SENTINEL_DETECTOR=cusum` (in progress) and BE-012f (in progress).
- Pre-registration of ADR-008 §G, all `not yet registered`: δ (→ `k`), persistence-test
  criterion, false-alarm budget (Q-TEAM-03), the region rule (Q-DET-05), artefact hash. Must be
  committed before any evaluation run.
- Open questions: Q-ML-07/08 (ML), Q-UI-05/06 (dashboard/evaluation), Q-TEAM-03, Q-DET-05,
  Q-DET-06 (onset inherited by a new episode). Dependencies DEP-DET-04/05.
- ADR-007/008 are `Proposed`: they need the ML owner (Q-ML-07), the dashboard owner (Q-UI-05)
  and the evaluation owner (Q-UI-06).

## Next recommended step

Finish the `SENTINEL_DETECTOR=cusum` selection and BE-012f, then integrate the real
forecaster/audit as they arrive (BE-011/013) on the shared API (`docs/runbook-vm.md`) and measure
p50/p99 with real models (BE-015, incl. the s390x run of the determinism test). Independent: BE-017.

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
| real models | **NOT_RUN** — blocked (see above) |
| branch `wip/be-012-docs` @ `a93883b` — x86-64 (Intel Core Ultra 7 155H), Python 3.14.7: `just check` | ruff format: 75 files unchanged · ruff check OK · mypy: no issues in 75 source files · **299 passed, 8 skipped** (the 8 skips are the P1 data tests without `SENTINEL_DATA_DIR`) |
| same, `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data uv run pytest tests/test_p1_data.py -q` | **14 passed** |
| same, `uv run pytest tests/detection -q` | **178 passed** |
| detector determinism digest, x86-64 (`tests/detection/test_determinism.py`) | `d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413` · s390x: **NOT_RUN** (BE-015) |
| line coverage | **NOT_RUN** — no coverage tool configured |
