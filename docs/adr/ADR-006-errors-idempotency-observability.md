# ADR-006 — Errors, idempotency, audit failure and observability

- **Status:** Proposed — audit failure mode needs the audit owner (Q-AUD-03); status
  vocabulary needs the dashboard owner (Q-UI-02).
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
  accepted window. **Fail-open (proposed):** if the sink fails, the alert is still stored and
  served, `audit="failed"` and `audit_failures` is incremented. Alert payload is hashed
  **without** its `audit` field (avoids the circular reference in design doc §6 example).
- Errors: one envelope `{"error": {"code", "message", "details"}}` for 404/409/422/500.
- Observability: JSON-lines logs (`sentinel.*` loggers); per-stage latency samples
  (validation, temporal_policy, history, forecast, detection, audit, total,
  http_post_windows) with nearest-rank p50/p95/p99 over the last N; counters; throughput
  and error rate in `/v1/status`.

## Alternatives considered
- Reject (5xx) when forecasting fails: the replay would retry and either stall or skip; the
  observation itself is valid.
- Fail-closed audit (reject the window if audit fails): stronger guarantee, but an audit
  outage stops detection. Left to the audit owner (Q-AUD-03).
- Prometheus client: extra dependency; JSON in `/v1/status` is enough for the demo.

## Pros
Replay never stalls; every outcome is visible and counted.

## Cons
Fail-open audit can show alerts that are not in the log.

## Consequences
Dashboard must render `status`, `issues`, `is_mock` and `audit`.

## Risks
In-memory metrics reset on restart; percentiles over a sliding sample, not the whole run →
the replay client reports its own client-side p50/p99 too.

## Dependencies
Audit owner (failure mode, event schema); dashboard owner (status vocabulary).

## Revisit when
The audit owner chooses fail-closed, or metrics must be scraped by an external system.
