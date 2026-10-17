# ADR-004 — Temporal policy: order, duplicates, gaps, no reprocessing

- **Status:** Proposed — needs confirmation by the data owner (Q-DATA-02, Q-DATA-03) and
  the detection owner (Q-DET-03).
- **Date:** 2026-10-17

## Context
The replay sends 2019 as 5-minute windows in timestamp order (design doc §2, §7). CUSUM is
sequential: each window updates state from the previous one. A single valid payload does
not make a valid time series.

## Problem
What to do with repeated, conflicting, late, misaligned or missing windows, and whether a
window can be processed again.

## Decision
| Case | Rule | HTTP |
|---|---|---|
| `timestamp` without timezone | rejected | 422 `validation_error` |
| `timestamp` not on the 300 s UTC grid | rejected (configurable) | 422 `timestamp_misaligned` |
| Same timestamp, same measurements (`content_hash`, `source` ignored) | idempotent: stored result returned, nothing reprocessed | 200, `duplicate=true` |
| Same timestamp, different measurements | rejected | 409 `window_conflict` |
| Timestamp ≤ last accepted (and not a known duplicate) | rejected, not stored | 409 `out_of_order` |
| Timestamp > last + interval (gap) | accepted; `gap_windows` reported; contiguity resets | 201 |
| Contiguous past windows < required | accepted, not scored | 201, `insufficient_history` |

- The timestamp is the **end** of the 5-minute aggregation interval.
- No imputation of missing windows by the backend. After a gap, scoring resumes when the
  contiguous history is long enough again.
- No reprocessing: a window is processed exactly once. A failed window keeps its failed
  result; resending it returns that result. Rationale: re-running an old window after later
  windows were scored would corrupt the sequential detector state.

## Alternatives considered
- Accept late windows and re-sort: invalidates CUSUM state already advanced.
- Impute gaps (last value / interpolation): a modelling decision that belongs to ML/data.
- Allow `POST /v1/windows/{ts}/reprocess`: only safe for the latest window; deferred.

## Pros
Deterministic: the same replay produces the same results and alert IDs.

## Cons
One missing window blinds scoring for `min_history` intervals (12 h by default).

## Consequences
The replay client must send in order and never skip; it may resend safely.

## Risks
If the real dataset has gaps or NaNs, the default blinding is costly → Q-DATA-03.

## Dependencies
Data owner (gaps, missing values, timestamp semantics); detection owner (state after gaps).

## Revisit when
Gap statistics of the real data are known, or the detection owner defines how CUSUM state
should behave across gaps.
