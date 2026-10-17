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
  `deviation_m` stays observed − expected. Mock detector and examples updated.
- Timestamps without timezone are read as UTC (`SENTINEL_NAIVE_TIMESTAMP_TZ=UTC`, default) and
  flagged with `timestamp_tz_assumed` in the result and audit event; `reject` restores the 422.
  The HTTP input model is now `SensorWindowIn`; `SensorWindow` stays timezone-aware.

### Added
- Operator dashboard tracer (UI-001, P6): Streamlit page (`just dashboard api_url=...`) with
  the header from `/health` + `/v1/status` (simulated time, models, SIMULATED badge). Reads the
  backend only through `dashboard.source.AlertSource` (HTTP adapter, stdlib); every answer is
  parsed with the contract models (`ContractMismatchError` / `SourceUnavailableError`).
  Streamlit is in the optional `dashboard` dependency group (CI installs it; the runtime install
  is unchanged). Guard tests: no leak-label references in `dashboard/`, and the dashboard
  imports only `sentinel.schemas` from the backend.
- Dashboard playback (UI-002, P6): `PlaybackAlertSource` serves a recorded run (`alerts.jsonl`
  in stored order + `status.json` + optional `run.json`) with the API's list semantics, behind
  the same `AlertSource` seam; `SENTINEL_DASHBOARD_PLAYBACK_DIR` / `just dashboard-playback`
  switch to it and the page shows a permanent "RECORDED REPLAY — run <id>, <date>" banner and
  "recorded replay" instead of the API state. Fixture runs (`fixture-edge-cases`,
  `fixture-empty`, `fixture-malformed`) generated from the mock backend by
  `scripts/make_playback_fixtures.py` (`just playback-fixtures`). `AlertQuery` enforces the
  API bounds (`limit` 1–500, `offset` ≥ 0).
- Dashboard "Recent alerts" (UI-003, P6): table newest first with alert ID, window end, region
  ("region not available"), deviating-sensor count, strongest deviation and its z ("not
  provided" when null), estimated onset, age on the simulated clock (`status.last_timestamp`
  minus `window_end`) and a per-alert SIMULATED badge; empty state, loading spinner and a
  contract-mismatch state for alerts. Works with the HTTP and playback sources.
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

## [0.0.0] — 2026-10-17

- Repository bootstrap.
