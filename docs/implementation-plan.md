# Implementation plan — Backend & Ingestion

**Objective:** a stable, tested ingestion and orchestration layer that the ML, detection,
audit and dashboard modules plug into without backend changes.
**Scope:** everything in `docs/architecture.md` "Components" owned by Backend & Ingestion.
Out of scope: GRU/XGBoost training, final CUSUM logic, hash-chained audit, narrative,
dashboard UI.

Status values: `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE` (DONE = acceptance criterion met
**and** its tests run green; file creation alone is not DONE). Effort: S (< ½ day),
M (½–2 days), L (> 2 days).

## Tasks

| ID | Task | Prio | Status | Depends on | Owner | Effort | Acceptance criterion (verifiable) |
|---|---|---|---|---|---|---|---|
| BE-001 | Project structure, uv, ruff, mypy, pytest, justfile | P0 | DONE | — | Backend | S | `just check` green |
| BE-002 | Contracts v1.0 (window, forecast, detection, alert, audit) + examples | P0 | DONE (contract `Proposed`) | — | Backend | M | `tests/test_schemas.py`, `tests/test_contract_examples.py` green |
| BE-003 | Window validation: shape (Pydantic), sensor registry, timestamp grid | P0 | DONE (provisional IDs) | BE-002 | Backend | S | `tests/test_ingestion.py`, API 422 tests green |
| BE-004 | In-memory history (capacity, contiguity, gaps, concurrency) | P0 | DONE | BE-002 | Backend | S | `tests/test_history.py` green |
| BE-005 | Forecaster / Detector / AuditSink ports + labelled mocks | P0 | DONE | BE-002 | Backend | S | pipeline tests use only the ports |
| BE-006 | Pipeline: temporal policy, failure containment, idempotency, alerts, audit events | P0 | DONE (ADR-004/006 `Proposed`) | BE-003..005 | Backend | M | `tests/test_pipeline.py` green |
| BE-007 | HTTP API: `/health`, `POST /v1/windows`, `/v1/status`, `/v1/alerts`, `/v1/alerts/{id}`, error envelope | P0 | DONE | BE-006 | Backend | S | `tests/test_api.py` green |
| BE-008 | Synthetic generator, replay client, end-to-end replay test | P1 | DONE | BE-007 | Backend | S | `tests/test_integration.py` green; `scripts/replay.py` run recorded in `docs/performance.md` |
| BE-009 | Metrics (per-stage latency, counters, throughput, error rate), JSON logs, benchmark | P1 | DONE (x86, mocks) | BE-006 | Backend | S | `/v1/status` latency fields tested; baseline in `docs/performance.md` |
| BE-010 | Real sensor registry | P0 | DONE | DEP-DATA-01 | Backend + Data | S | official registry `config/sensors.battledim.json` (data owner) via `SENTINEL_SENSOR_REGISTRY`; full 2019 replay validated with 0 rejections (`scripts/validate_replay_ingestion.py`) |
| BE-011 | Integrate real forecaster(s) behind `Forecaster` | P0 | DONE 2026-10-17 (`SENTINEL_FORECASTER=xgboost` + `_PATH`, `_CONTRIBUTIONS`, `_ENGINE`, `_TREE_FORMAT`; full 2019 in process on s390x with the real model, `docs/performance.md`) | DEP-ML-01..03 | Backend + ML | M | `SENTINEL_FORECASTER=<name>` loads at startup; contract tests with the real class green |
| BE-012 | Integrate real detector behind `Detector` (umbrella of BE-012a..i, vertical slices) | P0 | IN_PROGRESS (BE-012a..e DONE as `CusumDetector`; the `SENTINEL_DETECTOR=cusum` selection is DONE in `build_pipeline`; BE-012f in progress, parts 1 and 2 done; BE-012g and BE-012i DONE; BE-012h BLOCKED; final calibration waits for DEP-DET-05) | BE-012a..i, DEP-DET-01..06, ADR-007/008/011 | Backend + Detection | L | `SENTINEL_DETECTOR=cusum` loads at startup with a calibration artefact whose `provenance` is `battledim-2018-oof`; existing pipeline/API tests pass unchanged with the real class injected. **Selection DONE** (any provenance): it loads at startup from `SENTINEL_DETECTOR_CALIBRATION` (+ optional `SENTINEL_DETECTOR_REGIONS` and `SENTINEL_DETECTOR_NETWORK`), refuses with `ConfigError` a missing variable or file, an invalid file, another forecaster, other or reordered sensors and a mapping that does not cover the registry, and `mock` stays the default (`tests/test_detector_startup.py`, `tests/test_config.py`; also checked once against the real server with synthetic data, `docs/testing.md`). **Missing:** an artefact with `provenance = battledim-2018-oof` (BE-012h) |
| BE-012a | Tracer bullet: synthetic step → simplified CUSUM → `is_mock` alert, end to end | P0 | DONE (superseded by the BE-012b..d core; the tests remain) | — | Detection + Backend | M | `tests/detection/test_end_to_end.py`: through `POST /v1/windows` with `CusumDetector` injected via `build_pipeline` and an invented `provenance = synthetic` artefact, a synthetic day with a step on 3 pressure sensors gives exactly one alert in `GET /v1/alerts` with `is_mock = true` on the alert and on `models.detector`, `cusum.statistic > cusum.threshold`, `deviating_sensors` = the stepped sensors; `normal` and no alert before the step; no numeric default in code. The `SENTINEL_DETECTOR=cusum` selection was not part of this slice; it was wired later under the BE-012 umbrella (`tests/test_detector_startup.py`) |
| BE-012b | Per-sensor episodes: claim and release (`h_close`), cap `c·h`, no reset, concurrent alerts, `normal`/`suspect`/`alarm`, onset | P0 | DONE | BE-012a, ADR-007 | Detection | M | `tests/detection/test_cusum.py`, `test_end_to_end.py`: two steps on disjoint sensor groups give two alerts; after a step is removed its sensors are released within the time bounded by the cap and a new step opens a new alert; `detector_state` and `suspected_onset` (last window with `S = 0`; `null` when a deviating sensor never had `S = 0`) follow ADR-007 §5–6 |
| BE-012c | Gaps, restart and atomic rejection: `S` decays by `k` per missing window, bad input raises `DetectionError` with state unchanged | P0 | DONE | BE-012a, ADR-007 | Detection | M | `tests/detection/test_cusum.py`: `skipped_windows` and `reset_reason="gap"` when a skip empties every `S`; a run with failed windows equals a run fed only the passed windows; `timestamp ≤` last or without timezone, sensor set ≠ artefact, a non-finite value or a non-finite `z` → `DetectionError` with the state equal before and after (fault injection at every step); a claimed sensor is released when the gap takes it below `h_close` |
| BE-012d | `calibrate()` and startup checks: forecaster identity, sensor IDs and order, `provenance` → `is_mock`, `ModelInfo.version` with artefact hash | P0 | DONE | BE-012a, ADR-008 | Detection + Backend | M | `tests/detection/test_calibration.py`, `test_properties.py`, `test_determinism.py`: `calibrate()` (σ = max(floor, 1.4826·MAD), everything else a parameter without default) recovers a known σ from synthetic AR(1) residuals with diurnal heteroscedasticity and common mode; `write_artefact` + `CusumDetector.for_pipeline` start the server with the written file; a registry/order/forecaster mismatch raises `ConfigError`; property tests (larger step detected no later, scale invariance, null-series ARL0); determinism digest recorded for BE-015 (x86-64; s390x pending) |
| BE-012e | Region ranking with a synthetic sensor → region mapping | P1 | DONE | BE-012b | Detection | S | `tests/detection/test_regions.py`: a step on the sensors of region R gives `alert.region = R` and `GET /v1/alerts?region=R` returns it; `region_scores` (every region, `0.0` when empty) leave out claimed sensors; `regions_id` in `diagnostics`; startup fails if the mapping does not cover exactly `registry.pressure`; without a mapping `region` and `region_scores` stay null |
| BE-012f | Development calibration on 2018 in `src/sentinel/detection/offline/` (out-of-fold hour-of-week baseline) | P1 | IN_PROGRESS (unblocked: BE-010 on `main`, data v1 available locally). Part 1 DONE: out-of-fold residuals (`baseline.py`, `tests/detection/test_offline_baseline.py`). Part 2 DONE: replay with exact parity with the detector, parameter sweep and layer-1 counts (`sweep.py`, `layer1.py`; the matching rule is provisional, Q-UI-06; `tests/detection/test_offline_sweep.py`, `test_offline_layer1.py`). Pending: the development run that produces the artefact and the numbers | BE-012b, BE-012d, BE-010, data v1 | Detection | M | artefact with `provenance = dev-baseline-2018` (`is_mock = true`); residual structure, σ sensitivity and delay × false-alarms-per-week mechanics with counts n/N; reproduces from the data v1 hashes; no 2019 file opened. Parts 1 and 2 are tools (synthetic tests; the real-data test checks shape and finiteness only); no artefact and no result of a run exist yet |
| BE-012g | Real sensor → region mapping for L-Town | P1 | DONE 2026-10-17 | BE-012e, DEP-DET-04, Q-DET-05 | Detection | S | `config/regions.battledim.json` (nine regions over the 33 official pressure sensors) built by the rule of ADR-011 §2 from the pipe network, the data owner's `$SENTINEL_DATA_DIR/network/v1/l-town.json` (sha256 `ff772dc1…`, outside git), by `python -m sentinel.detection.offline.topology`; fixed from the topology before any evaluation (no label, residual or 2019 file read); pinned by `tests/detection/test_topology_files.py`, and `topology --check` verifies it against the network file on any machine, the VM included; the server starts with the official registry, the regions and the network (`tests/test_detector_network_startup.py`) |
| BE-012i | Candidate pipe per alert (Q-DET-07, DEP-DET-06) | P1 | DONE 2026-10-17 | BE-012g, ADR-011 | Detection + Backend | S | `Alert` 1.1 with the optional `candidate_pipe` (`AlertCandidate` too); `PipeNetwork.candidate_pipe` = pipe whose midpoint minimises `Σ S·d²` over the deviating sensors; `SENTINEL_DETECTOR_NETWORK` (the data owner's `l-town.json`) with startup refusals; `tests/detection/test_network.py`, `test_candidate_pipe.py` (detector alone, then HTTP with a synthetic chain and with the real network), `tests/test_detector_network_startup.py`. Not claimed: that the candidate is the leak (6 abrupt 2018 leaks, development calibration: 5 of 6 within about 360 m, ADR-011 Evidence) |
| BE-012h | Final calibration and layer-1 evaluation (closes BE-012) | P0 | BLOCKED | BE-012f, BE-012g, BE-011, DEP-DET-05, Q-ML-07, Q-ML-08, Q-TEAM-03 | Detection + Backend | M | persistence test on the 6 abrupt 2018 leaks with the pre-registered criterion; delay × false-alarm curve per forecaster and leak type; artefact with `provenance = battledim-2018-oof` committed with its hash; detection fields of the ADR-008 G table filled; `SENTINEL_DETECTOR=cusum` with the real forecaster starts with `models.detector.is_mock = false`; no 2019 file opened |
| BE-013 | Integrate real audit sink | P1 | BLOCKED | DEP-AUD-01 | Backend + Audit | M | `SENTINEL_AUDIT_SINK=<name>`; chain verified after a replay |
| BE-014 | Replay of real 2019 data end to end | P1 | DONE (mocks; 105 120 × 201 over HTTP on the shared API, `docs/performance.md`) | DEP-DATA-05 | Backend + Data | S | full 2019 replay with 0 rejected windows (or every rejection explained) |
| BE-015 | Run on LinuxONE (s390x): install, gates, p50/p99 per window | P1 | IN_PROGRESS (mock baseline done; real forecaster measured in process and over HTTP; mypy and the real detector pending; detector determinism digest `d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413` identical on x86-64 and s390x — `tests/detection/test_determinism.py` passed on the VM 2026-10-17; BE-012i then added the `candidate_pipe` key to the serialised alert, so the literal is now `7c5ef97bf8ccee4e19695475142658b426be29f796298314ab054a277ad3eee3` (the replay without that key still gives `d9106c15e4d5…`) and its s390x run is **pending**; HTTP run with XGBoost done on a private port) | DEP-PLAT-01, Q-PLAT-02, BE-011/012 | Backend + Platform | M | `just check` on s390x; numbers in `docs/performance.md` |
| BE-016 | Dashboard needs (CORS, extra filters) | P2 | TODO | Q-UI-01..03 | Backend + UI | S | dashboard reads the API |
| BE-017 | Load test (HTTP p50/p99 at replay speed and saturation, keep-alive client) | P2 | TODO (sequential full-year HTTP baseline exists, BE-014) | BE-008 | Backend | S | numbers + method in `docs/performance.md` |
| BE-018 | CI (GitHub Actions running `just ci`) | P2 | DONE | BE-001 | Backend | S | workflow green on PR |
| BE-019 | Apply team decisions: fail-closed audit with pending queue (Q-AUD-03) and z sign convention (Q-DET-02) | P0 | DONE | — | Backend | S | `tests/test_audit_fail_closed.py`, `test_pressure_drop_has_negative_deviation_and_positive_z` green; ADR-006 and contract §3–§5 updated |
| BE-020 | Shared integration API on the VM: one ID scheme, `scripts/shared_api.sh`, runbook | P0 | DONE | BE-010 | Backend | S | API on port 8000 from `shared/civicwater/main` via the script; start/status/stop verified on the VM; `docs/runbook-vm.md` |
| BE-021 | Redesign the Streamlit operator dashboard | P1 | DONE 2026-10-17 | UI-001..005, ADR-009 | Dashboard | M | Alerts first, 16/14 px alert text, retained full records and expandable system status; source-derived diagrams and reduced-motion-aware local GSAP; format/lint/strict typing green; **741 passed, 34 skipped**; five Chrome viewport sizes, keyboard focus, contrast and 200% text checked (`docs/dashboard-design.md`, `docs/dashboard-ux-review.md`) |

## P6 — Dashboard & evaluation

BE-021 readability refinement: **DONE 2026-10-17** — issued-alert summaries first, all fields
retained, technical metrics in a disclosure and 16 px main / 14 px supporting alert text.
UI/UX Pro Max review also corrected headings, explicit-action focus, count semantics,
contrast and enlarged-text reflow. Full gates: **741 passed, 34 skipped**; browser checks
passed. Owner visual validation pending on the same branch/PR.

Owner: P6. Spec and tickets: `.scratch/p6-dashboard-evaluation/` (spec + one file per
ticket). Deadline 2026-10-18 10:00. UI-* are Tier 1 (mandatory); EV-* are Tier 2 and run
only if the real detector is plugged into the VM API by 2026-10-17 22:00 (EV-003 gate).
Details and acceptance criteria are in each ticket file. ADR-007/008 and Q-UI-05/06,
Q-DET-06 belong to the detection owner (BE-012, merged in PR #5); P6 uses ADR-009
(dashboard), ADR-010 (layer-2 evaluation), Q-UI-07, Q-DET-07.

| ID | Ticket | Task | Prio | Status | Depends on | Owner | Effort |
|---|---|---|---|---|---|---|---|
| UI-001 | 01 | Tracer: dashboard shows the live API status (HTTP adapter, contract parsing, label guard) | P0 | DONE | — | P6 | S |
| UI-002 | 02 | Playback adapter + contract fixtures, RECORDED REPLAY banner | P0 | DONE | UI-001 | P6 | S |
| UI-003 | 03 | "Recent alerts" list (null-safe, simulated clock, SIMULATED badge) | P0 | DONE | UI-002 | P6 | S |
| UI-004 | 04 | Alert detail with structured evidence | P0 | DONE | UI-003 | P6 | S |
| UI-005 | 05 | Polling, dedup, failure states, paginated history with filters | P1 | DONE | UI-003 | P6 | M |
| UI-006 | 06 | Cross-team proposals (Q-UI-07, Q-AUD-06/07, Q-DET-07, Q-UI-03/04) + ADR-009 | P1 | DONE | — | P6 | S |
| UI-007 | 07 | Demo runbook and contingency rehearsal (C5) | P0 | IN_PROGRESS (runbook + 2 technical rehearsals done; presenter rehearsal pending) | UI-004, UI-005 | P6 | S |
| EV-001 | 08 | ADR-010 layer-2 evaluation protocol (answers Q-UI-06 of P4's ADR-008), accepted before any 2019 run | P1 | IN_PROGRESS (ADR-010 written; P4 review + Accepted pending) | — | P6 (+P4 review) | S |
| EV-002 | 09 | Evaluation metrics core and report (header, watermark) | P1 | DONE | EV-001 | P6 | S |
| EV-003 | 10 | Layer-2 2019 run through the API (ADR-008), recorder + manifest, playback-compatible output | P1 | IN_PROGRESS (recorder + CLI done; the 2019 run waits for the final calibration, DEP-DET-05, and Q-DATA-07) | EV-002, UI-002 | P6 | M |

## Order of execution

1. Done: BE-001 → BE-002 → BE-003/004/005 → BE-006 → BE-007 → BE-008/009.
2. Done: BE-018 (CI). Independent next: BE-017 (load test), BE-016 once Q-UI answered.
3. As owners deliver: BE-011, BE-012, BE-013, then BE-015 (BE-010 and BE-014 are done).
4. BE-012: BE-012a..e and the `SENTINEL_DETECTOR=cusum` selection done; BE-012f in progress
   (parts 1 and 2 done) → BE-012g and BE-012i (done, ADR-011) → BE-012h.

**BE-012 slices.** Each slice is vertical (detector, wiring and a test through the pipeline or
the API) and demoable on its own. The detector is injected through `build_pipeline` in the
tests with a test-only forecaster double that returns the noise-free synthetic profile,
because `PersistenceMockForecaster` follows the step and the residual would last one window
only (ADR-007 §1). `just dev` keeps the mock detector unless `SENTINEL_DETECTOR=cusum` and a
calibration file are given; with the persistence mock a ramp accumulates and a step does not
(README).
BE-012a..i are owned by the detection owner and tracked here because BE-012 integrates their
result; the CUSUM logic itself is not backend scope.

## Integration points

| With | Where | Contract |
|---|---|---|
| Data & replay | `POST /v1/windows`, `config/sensors.*.json`, `scripts/replay.py` | contracts §1 |
| ML | `Forecaster` in `build_pipeline` | contracts §2, ADR-005 |
| Detection | `Detector` in `build_pipeline` | contracts §3, ADR-005 |
| Audit | `AuditSink` in `build_pipeline` | contracts §4, ADR-006 |
| Dashboard / evaluation | `GET /v1/alerts`, `GET /v1/status` | contracts §5 |

## Required tests per task

New behaviour = new test in the same PR. Real modules must pass the existing pipeline/API
tests unchanged when injected through `build_pipeline` (they are written against the ports).

## Risks

| Risk | Mitigation |
|---|---|
| Contracts change after owners review | versioning rules (contracts §6); questions answered early (P0 first) |
| s390x packages missing | test on the VM as soon as available (BE-015) |
| Gap policy too strict for real data | Q-DATA-03 with counts; ADR-004 revisit |
| Mock results mistaken for detections | `is_mock` everywhere, startup warning, docs |
| Separate repository fragments the submission | Q-TEAM-02, `git subtree` path |
