# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow SemVer;
contract versions are tracked separately (`schema_version`).

## [Unreleased]

### Changed
- One sensor-ID scheme: the official registry `config/sensors.battledim.json` (data owner);
  loading `data/vN/sensors.csv` as a registry was removed (it used CSV column names such as
  `Pressure_n1`, incompatible with the replay). Registry JSON may carry optional `units`.
- Audit is fail-closed (team decision Q-AUD-03): alerts are served only after their audit
  event is recorded; failed events wait in an ordered pending queue retried before each window;
  503 `audit_backlog_full` when `SENTINEL_AUDIT_PENDING_CAPACITY` is reached. Window `audit`
  is now `recorded | pending | disabled`; `/v1/status` reports the backlog.
- z sign convention (Q-DET-02): `z = (expected − observed)/σ`, positive on a pressure drop;
  `deviation_m` stays observed − expected. Mock detector and examples updated. The CUSUM
  accumulates this `z` as is (ADR-007 §2 corrected: no `d = −z`).
- Timestamps without timezone are read as UTC (`SENTINEL_NAIVE_TIMESTAMP_TZ=UTC`, default) and
  flagged with `timestamp_tz_assumed` in the result and audit event; `reject` restores the 422.
  The HTTP input model is now `SensorWindowIn`; `SensorWindow` stays timezone-aware.

### Added
- CUSUM detector `CusumDetector` (BE-012a..e; ADR-007/008, contract §3): per-sensor `S` capped at
  `c·h`; alert episodes per sensor (claim, release below `h_close`, no reset);
  `normal`/`suspect`/`alarm`; onset (`suspected_onset` null when unknown); decay of `S` across
  missing windows (`skipped_windows`, `reset_reason="gap"`); atomic rejection with
  `DetectionError`; calibration artefact with provenance, forecaster identity and sha256
  (`calibrate()`, `write_artefact`, `CusumDetector.for_pipeline` with `ConfigError` on a
  registry/order/forecaster mismatch; `is_mock` unless `provenance = battledim-2018-oof`);
  optional sensor → region mapping (`region`, `region_scores`, `diagnostics.regions_id`).
  Tested with invented calibrations. Determinism digest for the s390x parity run (BE-015) in
  `tests/detection/test_determinism.py`.
- Startup selection of the CUSUM detector (BE-012): `SENTINEL_DETECTOR=cusum` with
  `SENTINEL_DETECTOR_CALIBRATION` (required with `cusum`) and `SENTINEL_DETECTOR_REGIONS`
  (optional). `build_pipeline` calls `CusumDetector.for_pipeline` with the forecaster actually
  served; a missing variable, a missing or invalid file, a calibration of another forecaster,
  other or reordered sensors, a mapping that does not cover the registry, and either file set
  with the mock detector are a `ConfigError` before the server listens. `mock` stays the
  default; the detector is `is_mock=true` unless the calibration provenance is
  `battledim-2018-oof` (none exists yet). New settings fields `detector_calibration_path` and
  `detector_regions_path`; tests in `tests/test_detector_startup.py` and `tests/test_config.py`;
  usage in the README ("Running with the CUSUM detector").
- Offline tools of the CUSUM calibration (BE-012f, development only, never imported by the
  server; pandas is a dev dependency) in `src/sentinel/detection/offline/`: `baseline.py`
  (out-of-fold hour-of-week residuals with monthly folds, reproducible residual and fold files
  with their hashes, 2018 loader checked against `MANIFEST.json`), `sweep.py` (`replay` with exact
  parity with `CusumDetector` and a parameter `sweep`) and `layer1.py` (delay and false-alarm
  counts; the matching rule is a required argument and provisional, Q-UI-06). The sweep showed that
  false alarms are not monotone in `h` (ADR-008 §E). BE-012f is not finished: no artefact has
  been produced from a run yet.
- Documentation: end-to-end check of the CUSUM selection against the real server with synthetic
  data (`docs/testing.md`) and its timings (`docs/performance.md`); ADR-007/008, contract §3,
  architecture, dependencies and plan updated. No schema change.
- `scripts/validate_replay_ingestion.py`: runs a replay JSONL (e.g. the data owner's
  `windows_2019.jsonl`) through the pipeline in process.
- `scripts/shared_api.sh` and `docs/runbook-vm.md`: the shared integration API on the VM
  (running on port 8000 with the full 2019 replay loaded, mocks).
- First LinuxONE (s390x) baseline: tests, benchmark, HTTP replay and x86/s390x hash parity
  (`docs/performance.md`).
- GitHub Actions CI running `just ci` on pull requests and pushes to `main`.
- Contracts v1.0 (`Proposed`): window, forecast request/result, detection result, alert,
  audit event/ref; examples in `docs/examples/` validated by tests.
- Provisional sensor registry with placeholder IDs (33 pressure, 3 flow, 1 tank level).
- In-memory history buffer (default 288 past windows, scoring after 144 contiguous).
- Pipeline with temporal policy (duplicate → 200, conflict/out-of-order → 409, gaps
  reported), failure containment, idempotent resend, deterministic alert IDs, audit events.
- Ports `Forecaster`, `Detector`, `AuditSink` with labelled mocks
  (`mock-persistence`, `mock-threshold`, null / in-memory audit).
- HTTP API: `GET /health`, `POST /v1/windows`, `GET /v1/status`, `GET /v1/alerts`,
  `GET /v1/alerts/{alert_id}`, uniform error envelope.
- Per-stage latency metrics, counters, throughput, error rate; JSON-lines logs.
- Scripts: synthetic generator, replay client, in-process benchmark.
- ADR-001..006, implementation plan, dependencies, questions for the team, testing and
  performance docs, `AGENTS.md`, `CONTRIBUTING.md`.
- Tooling: uv, ruff, mypy (strict), pytest, justfile.
- Detector design (`Proposed`): ADR-007 (CUSUM statistic, per-sensor alert episodes,
  gap decay, atomicity, regions), ADR-008 (calibration and evaluation protocol), contract §3
  semantics, DEP-DET-05, BE-012 split into vertical slices BE-012a..h (BE-012a is a tracer
  bullet), questions Q-TEAM-03, Q-ML-07, Q-ML-08, Q-UI-05, Q-UI-06, Q-DET-06. No schema change.

## [0.0.0] — 2026-10-17

- Repository bootstrap.
