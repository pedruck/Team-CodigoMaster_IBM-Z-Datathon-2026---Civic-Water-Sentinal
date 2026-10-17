# Project status — Backend & Ingestion

**Last update:** 2026-10-17 — PR #1 (foundation) merged into `main`; PR #2 adds CI (BE-018).

## Summary

The backend runs end to end **with mocks**: windows are validated, ordered, buffered,
passed to a mock forecaster and a mock detector, alerts are stored and served, audit events
are emitted to a dev sink, and per-stage latency is measured. Nothing is integrated with the
real models, real sensor IDs, the real audit log or LinuxONE yet. All contracts are v1.0
`Proposed` until the other owners confirm them.

## Done (tests green)

BE-001 structure/tooling · BE-002 contracts + examples · BE-003 validation · BE-004 history ·
BE-005 ports + mocks · BE-006 pipeline · BE-007 HTTP API · BE-008 synthetic replay ·
BE-009 metrics/logs/benchmark (x86, mocks) · BE-018 CI (GitHub Actions). Details: `docs/implementation-plan.md`.

## In progress

None.

## Blocked

| Task | Waiting for |
|---|---|
| BE-010 real sensor registry | DEP-DATA-01 (data owner) |
| BE-011 real forecaster | DEP-ML-01..03 (ML owner) |
| BE-012 real detector | DEP-DET-01..03 (detection owner) |
| BE-013 real audit sink | DEP-AUD-01, Q-AUD-03 (audit owner) |
| BE-014 real 2019 replay | BE-010, DEP-DATA-05 |
| BE-015 LinuxONE run | DEP-PLAT-01 |

## Pending (independent, not started)

BE-016 dashboard needs (after Q-UI answers) · BE-017 load test with keep-alive client.

## External dependencies

22 open/partial items in `docs/dependencies.md`; P0 questions in
`docs/questions-for-team.md` (Q-TEAM-01/02, Q-DATA-01/02/03/06, Q-ML-01/02, Q-DET-01/02,
Q-AUD-01/03, Q-PLAT-01).

## Risks

- Separate repository may need to be merged into a team repository (Q-TEAM-02).
- Sensor IDs are placeholders; real data will be rejected until BE-010.
- Gap policy blinds scoring for 12 h after any missing window (ADR-004, Q-DATA-03).
- Fail-open audit can serve alerts missing from the log (ADR-006, Q-AUD-03).
- In-memory state: restart loses history, results, alerts, metrics, detector state (ADR-003).
- s390x wheels unverified (ADR-001).
- Design-doc inconsistencies flagged: residual/z sign (Q-DET-02), alert↔audit hash
  circularity (Q-AUD-05).

## Known bottlenecks

With mocks on x86, HTTP handling (p50 ≈ 4 ms server-side) dominates the pipeline
(p50 ≈ 0.4–0.8 ms). Both are far below the 300 s interval. See `docs/performance.md`.

## Future optimisations

Only with evidence from LinuxONE + real models: profile `content_hash`, keep-alive replay
client, out-of-process model adapter if a runtime cannot be imported (ADR-005).

## Next recommended step

Get answers to the P0 questions (start with Q-TEAM-01 owners and Q-DATA-06 sensor IDs), then
BE-010. Independent meanwhile: BE-017 (load test).

## Verification (2026-10-17, x86_64, Python 3.12.13)

| Command | Result |
|---|---|
| `just check` (ruff format, ruff check, mypy strict, pytest) | 52 files unchanged · ruff: all checks passed · mypy: no issues in 52 files · **89 passed** |
| `just bench 5000` | 5000 windows, total p50 0.3707 ms / p99 0.7358 ms (mocks) |
| uvicorn + `scripts/replay.py` (576 synthetic windows) | 576 × 201; resend 5 × 200; 1 mock alert; client p50 6.797 / p99 14.837 ms |
| `just dev` + README curl + `just replay` (fresh server) | example → 201 `insufficient_history`; 288 × 201; 1 mock alert |
| GitHub Actions run 39040317019 (`just ci`, ubuntu-latest, Python 3.12) | success · 52 files already formatted · ruff OK · mypy OK · **89 passed** |
| s390x / real models / real data | **NOT_RUN** — blocked (see above) |
| line coverage | **NOT_RUN** — no coverage tool configured |
