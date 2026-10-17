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
| BE-010 | Real sensor registry | P0 | BLOCKED | DEP-DATA-01, Q-DATA-06 | Backend + Data | S | `config/sensors.battledim.json`, `provisional: false`, default switched, tests green |
| BE-011 | Integrate real forecaster(s) behind `Forecaster` | P0 | BLOCKED | DEP-ML-01..03 | Backend + ML | M | `SENTINEL_FORECASTER=<name>` loads at startup; contract tests with the real class green |
| BE-012 | Integrate real detector behind `Detector` | P0 | BLOCKED | DEP-DET-01..03 | Backend + Detection | M | idem, `SENTINEL_DETECTOR=<name>` |
| BE-013 | Integrate real audit sink | P1 | BLOCKED | DEP-AUD-01, Q-AUD-03 | Backend + Audit | M | `SENTINEL_AUDIT_SINK=<name>`; chain verified after a replay |
| BE-014 | Replay of real 2019 data end to end | P1 | BLOCKED | BE-010, DEP-DATA-05 | Backend + Data | S | full 2019 replay with 0 rejected windows (or every rejection explained) |
| BE-015 | Run on LinuxONE (s390x): install, gates, p50/p99 per window | P1 | IN_PROGRESS (mock baseline done; mypy and real models pending) | DEP-PLAT-01, Q-PLAT-02, BE-011/012 | Backend + Platform | M | `just check` on s390x; numbers in `docs/performance.md` |
| BE-016 | Dashboard needs (CORS, extra filters) | P2 | TODO | Q-UI-01..03 | Backend + UI | S | dashboard reads the API |
| BE-017 | Load test (HTTP p50/p99 at replay speed and saturation, keep-alive client) | P2 | TODO | BE-008 | Backend | S | numbers + method in `docs/performance.md` |
| BE-018 | CI (GitHub Actions running `just ci`) | P2 | DONE | BE-001 | Backend | S | workflow green on PR |

## Order of execution

1. Done: BE-001 → BE-002 → BE-003/004/005 → BE-006 → BE-007 → BE-008/009.
2. Done: BE-018 (CI). Independent next: BE-017 (load test), BE-016 once Q-UI answered.
3. As owners deliver: BE-010 (unblocks BE-014), BE-011, BE-012, BE-013, then BE-015.

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
