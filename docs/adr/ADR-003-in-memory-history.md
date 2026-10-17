# ADR-003 — In-memory, bounded storage for history, results and alerts

- **Status:** Accepted (stage 1 only)
- **Date:** 2026-10-17
- **Deciders:** Backend & Ingestion owner.

## Context
The forecaster needs the last 12–24 h of windows (design doc Tables 2–3). The audit log is
the system of record for decisions and belongs to another owner.

## Problem
Where to keep the rolling history, the per-window results (for idempotency) and alerts.

## Decision
Process memory, bounded:
- history: `deque` of `SENTINEL_HISTORY_CAPACITY` past windows (default 288 = 24 h) + current;
- results: last `SENTINEL_RESULT_CAPACITY` window results (default 2016 = 7 days);
- alerts: last `SENTINEL_ALERT_CAPACITY` alerts (default 10 000).
Scoring requires `SENTINEL_MIN_HISTORY_WINDOWS` contiguous past windows (default 144 = 12 h),
or more if the forecaster declares it.

**Everything is lost when the process restarts.** After a restart the service needs
`min_history` windows again before scoring, and the detector state (CUSUM) starts from zero.

## Alternatives considered
- SQLite/Postgres: durable, but adds schema/migrations before the data owner confirmed the
  window format; durability of decisions is the audit log's job.
- Redis: external dependency and another s390x package to validate.

## Pros
Zero infrastructure; O(1) append; trivially testable.

## Cons
No durability; single process only (no horizontal scaling: state is per process).

## Consequences
Run exactly one worker (`uvicorn` default). Replay after a restart must resend at least the
last `min_history` windows before the restart point; the backend accepts the first window
after restart at any timestamp.

## Risks
Restart during the 2019 replay loses alerts not yet read by the dashboard → mitigated by the
audit log once it exists (DEP-AUD-01).

## Dependencies
Audit owner's persistence (DEP-AUD-01).

## Revisit when
The demo requires surviving restarts, more than one worker, or the dashboard needs history
older than the alert capacity. Durable storage needs a new ADR.
