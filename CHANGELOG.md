# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow SemVer;
contract versions are tracked separately (`schema_version`).

## [Unreleased]

### Changed
- Timestamps without timezone are read as UTC (`SENTINEL_NAIVE_TIMESTAMP_TZ=UTC`, default) and
  flagged with `timestamp_tz_assumed` in the result and audit event; `reject` restores the 422.
  The HTTP input model is now `SensorWindowIn`; `SensorWindow` stays timezone-aware.

### Added
- Sensor registry loaded from the data owner's `data/vN/sensors.csv` (official IDs and units,
  shown in `/v1/status`); `scripts/validate_v1_ingestion.py` runs a full year of the export
  through the pipeline.
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

## [0.0.0] — 2026-10-17

- Repository bootstrap.
