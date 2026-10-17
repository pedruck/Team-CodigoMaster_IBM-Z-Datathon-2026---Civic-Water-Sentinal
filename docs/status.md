# Project status — Backend & Ingestion

**Last update:** 2026-10-17 — branch `zanetti/shared-api`: one sensor-ID scheme (official registry of the data owner), shared API script + runbook, full 2019 HTTP replay.

## Summary

The backend runs end to end **with mocks**: windows are validated, ordered, buffered,
passed to a mock forecaster and a mock detector, alerts are stored and served, audit events
are emitted to a dev sink, and per-stage latency is measured. Nothing is integrated with the
real models or the real audit log yet. The official sensor set is read from the data owner's
`config/sensors.battledim.json` (official registry, data owner), and the whole 2019 replay passes
validation with 0 rejections. On the LinuxONE VM (s390x) the
runtime installs from wheels and the 89 tests pass; mypy does not install there yet. All contracts are v1.0
`Proposed` until the other owners confirm them.

## Done (tests green)

BE-001 structure/tooling · BE-002 contracts + examples · BE-003 validation · BE-004 history ·
BE-005 ports + mocks · BE-006 pipeline · BE-007 HTTP API · BE-008 synthetic replay ·
BE-009 metrics/logs/benchmark (x86, mocks) · BE-010 real sensor registry · BE-018 CI (GitHub Actions) · BE-019 fail-closed audit + z sign
convention. Details: `docs/implementation-plan.md`.

## In progress

BE-014 real 2019 replay: in-process full year done; HTTP replay waits for the data owner's
replay tool (the backend now accepts the CSV timestamps as they are, read as UTC).

BE-015 LinuxONE: mock baseline, parity hash and HTTP replay done; full `just check`
waits for Q-PLAT-02 (`gcc`), model measurements wait for BE-011/012.

## Blocked

| Task | Waiting for |
|---|---|
| BE-011 real forecaster | DEP-ML-01..03 (ML owner) |
| BE-012 real detector | DEP-DET-01..03 (detection owner) |
| BE-013 real audit sink | DEP-AUD-01 (audit owner) |

## Pending (independent, not started)

BE-016 dashboard needs (after Q-UI answers) · BE-017 load test with keep-alive client.

## External dependencies

22 open/partial items in `docs/dependencies.md`; P0 questions in
`docs/questions-for-team.md` (Q-TEAM-01/02, Q-ML-01/02, Q-DET-01/02,
Q-AUD-01/03); Q-PLAT-01 answered, Q-PLAT-02 (P1) open.

## Risks

- Separate repository may need to be merged into a team repository (Q-TEAM-02).
- Gap policy blinds scoring for 12 h after any missing window (ADR-004); v1 has no gaps.
- Fail-closed audit: during an audit outage no new alerts are served; the pending queue is in
  memory and lost on restart (ADR-006).
- In-memory state: restart loses history, results, alerts, metrics, detector state (ADR-003).
- `mypy` not installable on s390x without `gcc` (Q-PLAT-02); model runtime wheels on s390x unverified.

## Known bottlenecks

With mocks on x86, HTTP handling (p50 ≈ 4 ms server-side) dominates the pipeline
(p50 ≈ 0.4–0.8 ms). Both are far below the 300 s interval. See `docs/performance.md`.

## Future optimisations

Only with evidence from LinuxONE + real models: profile `content_hash`, keep-alive replay
client, out-of-process model adapter if a runtime cannot be imported (ADR-005).

## Next recommended step

Run the HTTP replay of 2019 with the data owner's replay tool (BE-014); confirm Q-DATA-02 (UTC,
end of interval). Independent meanwhile: runbook for the
shared API instance on the VM (demo), and BE-017 (load test).

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
| real models | **NOT_RUN** — blocked (see above) |
| line coverage | **NOT_RUN** — no coverage tool configured |
