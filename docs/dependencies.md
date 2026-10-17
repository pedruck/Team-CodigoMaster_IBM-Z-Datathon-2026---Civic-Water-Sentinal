# External dependencies and blockers

States: `OPEN` (nothing received), `PARTIAL` (some information), `RESOLVED`.
Owners are named by role: the mapping role → team member is not recorded in the design
document (it lists the members, not their roles) — first item of `docs/questions-for-team.md`.

| ID | Owner (role) | Needed | Expected contract | Impact on backend | State | How to unblock | Temporary workaround | Done when |
|---|---|---|---|---|---|---|---|---|
| DEP-DATA-01 | Data & replay | Official IDs of the 33 pressure, 3 flow and 1 tank sensors | registry JSON (`config/sensors.*.json`) | registry validation, forecaster array order | RESOLVED 2026-10-17 — `data/v1/sensors.csv` (33 `Pressure_n*`, `Flow_p227`, `Flow_p235`, `Flow_PUMP_1`, `Level_T1`) loaded directly by the backend | — | placeholder JSON kept for dev/tests | done |
| DEP-DATA-02 | Data & replay | Units of pressure, flow, tank level | contract §1 table | detector thresholds, alert `deviation_m` | RESOLVED 2026-10-17 — `sensors.csv`: pressure m, flow m³/h, tank level m | — | — | done |
| DEP-DATA-03 | Data & replay | Timestamp semantics, timezone, grid, DST | contract §1 | alignment rule, ordering | PARTIAL — v1 CSV timestamps are naive (`2019-01-01 00:00:00`), on an exact 300 s grid, no DST artefacts (0 duplicates, 0 gaps) | Q-DATA-02 | replay appends `Z` (UTC), end-of-interval | confirmed by data owner |
| DEP-DATA-04 | Data & replay | Missing values / gaps in 2018–2019 series | ADR-004 | gap policy, blinding after gaps | RESOLVED for v1 — 2018 and 2019: 105 120 rows each, 0 gaps, 0 duplicates, 0 missing/non-numeric cells | — | gap policy kept for robustness | done (revisit if a later version has gaps) |
| DEP-DATA-05 | Data & replay | Replay format and client | JSONL of contract §1 windows, in order | replay tooling | PARTIAL | Q-DATA-04 | `scripts/replay.py` reads contract JSONL | real 2019 replay runs end to end |
| DEP-DATA-06 | Data & replay | Label policy | labels only in the evaluation pipeline | none at runtime (labels rejected) | PARTIAL | Q-DATA-05 | unknown fields rejected | evaluation script reads labels offline only |
| DEP-ML-01 | ML (forecasting) | Implementation of `Forecaster` | contract §2 | replaces mock | OPEN | Q-ML-01 | `PersistenceMockForecaster` (`is_mock`) | real class passes `tests/` contract tests and loads at startup |
| DEP-ML-02 | ML | Model artefacts and loader: XGBoost JSON (Snap ML), GRU ONNX | files + loader in `build_pipeline` | startup, dependencies | OPEN | Q-ML-03 | none | artefacts load on x86 and s390x |
| DEP-ML-03 | ML | Runtime deps and LinuxONE compatibility (Snap ML, onnxruntime/zDLC, PyTorch container) | optional dependency group | install on s390x | OPEN | Q-ML-04, DEP-PLAT-01 | none | `uv sync` works on the VM |
| DEP-ML-04 | ML | Latency per window, warm-up need, failure modes | numbers measured on the VM | p99 budget, timeouts | OPEN | Q-ML-05 | mock latency only | entries in `docs/performance.md` |
| DEP-DET-01 | Detection & localisation | Implementation of `Detector` (residual, CUSUM, region ranking) | contract §3 | replaces mock | OPEN | Q-DET-01 | `ThresholdMockDetector` (`is_mock`) | real class passes contract tests |
| DEP-DET-02 | Detection | Residual/z sign convention, σ, k, h, score units | contract §3 | alert evidence fields | OPEN | Q-DET-02 | `residual = observed − expected` | convention in contract §3 |
| DEP-DET-03 | Detection | State behaviour across gaps/restarts; alert closing | contract §3 | pipeline, alert lifecycle | OPEN | Q-DET-03, Q-DET-04 | no reset signal; alerts never close | ADR-004 accepted |
| DEP-DET-04 | Detection | Sensor → region mapping | used by detector only | `region` field, `/v1/alerts?region=` | OPEN | Q-DET-05 | `region = null` | mapping delivered with detector |
| DEP-AUD-01 | Audit & narrative | Real `AuditSink` (hash chain, persistence) | contract §4 | traceability, durability | OPEN | Q-AUD-01, Q-AUD-02 | `NullAuditSink` / `InMemoryAuditSink` | sink passes contract tests, chain verifiable |
| DEP-AUD-02 | Audit | Read access to the log (`GET /v1/audit`?) and permissions | to define | endpoint | OPEN | Q-AUD-04 | endpoint not created | contract + ADR |
| DEP-AUD-03 | Audit | Failure mode (fail-open vs fail-closed) | ADR-006 | acceptance semantics | OPEN | Q-AUD-03 | fail-open | ADR-006 accepted |
| DEP-AUD-04 | Narrative | Where the narrative is generated and stored; link to alert | to define | alert payload | OPEN | Q-AUD-05 | not in alert payload | contract update |
| DEP-UI-01 | Dashboard & evaluation | Endpoints, filters, pagination, status vocabulary | contract §5 | read API | PARTIAL | Q-UI-01, Q-UI-02 | current read API | dashboard consumes it |
| DEP-UI-02 | Dashboard | CORS origin / how the dashboard is served | config | browser access | OPEN | Q-UI-03 | no CORS middleware | origin configured |
| DEP-EVAL-01 | Evaluation | Retrospective 2019 metrics (detections, delay, false alarms, BattLeDIM score) | reads alerts + labels offline | none at runtime | OPEN | Q-UI-04 | none | evaluation pipeline exists |
| DEP-PLAT-01 | Platform (LinuxONE VM) | VM access, Python 3.12 on s390x, wheels for FastAPI/pydantic-core | VM image | runs at all | PARTIAL — VM available (Ubuntu 24.04, Python 3.12.3, NNPA exposed); runtime deps install from wheels; 89 tests pass; `mypy` blocked (no `gcc`, `librt` has no s390x wheel) | Q-PLAT-02 | run pytest/ruff with pinned `--with` (docs/testing.md) | `just check` green on s390x |
