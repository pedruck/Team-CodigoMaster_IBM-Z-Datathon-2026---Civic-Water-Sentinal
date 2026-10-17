# ADR-006 — Errors, idempotency, audit failure and observability

- **Status:** Proposed — audit failure mode decided by the team (fail-closed, Q-AUD-03,
  `docs/team-workflow.md`); status vocabulary still needs the dashboard owner (Q-UI-02).
- **Date:** 2026-10-17

## Context
Data must never be dropped silently, alerts must be traceable (design doc §5–§6) and the
team must report p50/p99 latency per window on LinuxONE (design doc §7).

## Problem
Define acceptance vs processing outcome, error formats, behaviour when a stage fails, and
what is measured.

## Decision
**Acceptance is separate from processing.** Once a window passes validation and the temporal
policy, it is accepted (201) and stored in history, whatever happens next. Its `status` is one
of `scored`, `insufficient_history`, `forecast_failed`, `detection_failed`; failures carry
`issues[]` (`stage`, `code`, `message`).

- Forecaster/detector exceptions are caught at the port boundary, logged with traceback,
  counted, and turned into `*_failed` results. Contract violations (wrong timestamp, missing
  sensors) are `invalid_forecast` / `invalid_detection`.
- Detector contract: `evaluate` is atomic — raise without changing state.
- Idempotency key: window `timestamp` + `content_hash`. Resends return the stored result
  (200); no duplicate alerts are possible. Alert IDs are deterministic
  (`A-<yyyymmddThhmm>Z-<n>`).
- Audit: `alert.raised` for each alert, `window.scored` / `window.not_scored` for every
  accepted window. Alert payload is hashed **without** its `audit` field (avoids the circular
  reference in the design doc §6 example; Q-AUD-05, decided).
- **Fail-closed audit (decided, Q-AUD-03):** an alert is served (`/v1/alerts`) only after its
  `alert.raised` event is recorded. If the sink fails, the window is still accepted and
  processed (the detector already advanced), its events go to an in-memory **pending queue**
  in window order, the result says `audit="pending"`, and the alert is withheld. Every new
  window first retries the queue; new events queue behind pending ones, so the chain keeps
  window order. Recorded alert events publish their alert with its `AuditRef`. When the queue
  reaches `SENTINEL_AUDIT_PENDING_CAPACITY` (default 2016 events ≈ 7 days of windows), new
  windows are refused with 503 `audit_backlog_full` and nothing changes, so the replay can
  retry. `/v1/status` shows `audit_pending_events`, `audit_pending_alerts`,
  `audit_failures`, `audit_retried`. With the `null` sink (audit disabled, development)
  alerts are served immediately and `audit="disabled"`.
- Errors: one envelope `{"error": {"code", "message", "details"}}` for 404/409/422/500/503.
- Observability: JSON-lines logs (`sentinel.*` loggers); per-stage latency samples
  (validation, temporal_policy, history, forecast, detection, audit, total,
  http_post_windows) with nearest-rank p50/p95/p99 over the last N; counters; throughput
  and error rate in `/v1/status`.

## Alternatives considered
- Reject (5xx) when forecasting fails: the replay would retry and either stall or skip; the
  observation itself is valid.
- Fail-open audit (serve the alert even if not logged): was the first proposal; rejected by
  the team because an alert that sends a crew must be traceable (design doc §5).
- Fail-closed by rejecting the window: an audit outage would stop detection and lose the
  detector's sequential state; queuing keeps detection running and only withholds alerts.
- Prometheus client: extra dependency; JSON in `/v1/status` is enough for the demo.

## Pros
Replay never stalls; every outcome is visible and counted.

## Cons
During an audit outage operators see no new alerts (by design); the pending queue lives in
memory and is lost on restart, like the rest of the state (ADR-003). There is no retry
without a new window: if the replay stops, pending events wait for the next one.

## Consequences
Dashboard must render `status`, `issues`, `is_mock` and `audit`.

## Risks
In-memory metrics reset on restart; percentiles over a sliding sample, not the whole run →
the replay client reports its own client-side p50/p99 too.

## Dependencies
Audit owner (failure mode, event schema); dashboard owner (status vocabulary).

## Revisit when
The real audit sink needs its own retry/persistence, pending events must survive restarts, or
metrics must be scraped by an external system.

Change log: 2026-10-17 — fail-open replaced by fail-closed with a pending queue (team
decision Q-AUD-03); window `audit` vocabulary is now `recorded | pending | disabled`.
