# Architecture Decision Records

One file per decision with impact on contracts, reliability, performance or integration:
`ADR-NNN-short-title.md`, numbered in order, never renumbered.

## Status values

| Status | Meaning |
|---|---|
| `Proposed` | Implemented or planned, but depends on confirmation by another owner. Code may follow it; contracts may still change. |
| `Accepted` | Confirmed by the owners it affects. Changing it requires a new ADR. |
| `Rejected` | Considered and discarded; kept for history. |
| `Superseded` | Replaced by a later ADR (link it). |

## Rules

- Do not edit the decision of an `Accepted` ADR; write a new one that supersedes it.
- A `Proposed` ADR lists the open question and the owner who must confirm it, and links
  the entry in `docs/questions-for-team.md`.
- Every ADR has: status, context, problem, decision, alternatives, pros, cons,
  consequences, risks, dependencies, revisit conditions.
- Code that implements an ADR references it in a docstring or comment (`ADR-004`).

## Index

| ADR | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-python-fastapi.md) | Python + FastAPI for the application layer | Accepted |
| [ADR-002](ADR-002-module-separation.md) | Ports between API, history, forecasting, detection and audit | Accepted |
| [ADR-003](ADR-003-in-memory-history.md) | In-memory, bounded storage for history, results and alerts | Accepted (stage 1) |
| [ADR-004](ADR-004-temporal-policy.md) | Temporal policy: order, duplicates, gaps, no reprocessing | Proposed |
| [ADR-005](ADR-005-model-integration.md) | Integration of the real models in process, behind ports | Proposed |
| [ADR-006](ADR-006-errors-idempotency-observability.md) | Errors, idempotency, audit failure and observability | Proposed |
| [ADR-007](ADR-007-cusum-detector.md) | CUSUM detector: statistic, alert lifecycle, gaps, atomicity, regions | Proposed |
| [ADR-008](ADR-008-calibration-and-evaluation.md) | Detector calibration and evaluation protocol | Proposed |
| [ADR-009](ADR-009-dashboard.md) | Operator dashboard: server-side, contract-checked, read-only | Proposed |
| [ADR-010](ADR-010-layer2-evaluation.md) | Layer-2 evaluation of 2019 through the API: matching rule, metrics, manifest | Proposed |
