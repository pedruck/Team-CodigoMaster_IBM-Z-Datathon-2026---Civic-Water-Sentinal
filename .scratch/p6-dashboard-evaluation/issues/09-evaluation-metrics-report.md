# 09: EV-002 — Evaluation metrics core and report

**What to build:** Given a set of alerts, the 2019 leak table and the ADR-010 matching parameters, the evaluation produces:
- detected and missed leaks;
- the detection delay distribution (median, p90);
- the onset error, when `suspected_onset` is present;
- false alarms per week;
- a per-leak table.

It renders a report that always carries the "simulated data (BattLeDIM L-Town); not validated on a real network" header. When the run used mocks or provisional sensor IDs, the report carries the "NOT A RESULT" watermark. The matching logic is a pure function with no I/O.

**Blocked by:** 08 (ADR-010)

**Status:** done (2026-10-17)

- [x] Tests with hand-made tables: alert before start, inside the window, after Tmax; two alerts for one leak; alert with no leak; a leak active at year start; `suspected_onset` null
- [x] Tests: the header is always present; the watermark appears for mock or provisional runs
- [x] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`

## Comments

- 2026-10-17 — Done (TDD, 14 tests on a hand-made worked example checked independently by a
  reviewer). Reuses P4's `layer1.evaluate`; no second matching implementation.
- Review fixes: delay from peak and max; guard for label windows longer than 731 days; inherited
  leaks and false-alarm times listed; "time-only hits" caveat; manifest gains registry and
  `uv.lock` hashes and the protocol version; the new-in-year view no longer prints false alarms;
  dirty tree watermark added to ADR-010 §9.
- Debt: `_nearest_rank` duplicates `observability.metrics._percentile`; `Manifest` lives in the
  report module; the onset error takes the first alert with an onset among those sharing the
  detection `window_end`.
