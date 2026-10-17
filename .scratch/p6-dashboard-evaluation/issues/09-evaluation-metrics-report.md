# 09: EV-002 — Evaluation metrics core and report

**What to build:** Given a set of alerts, the 2019 leak table and the ADR-008 matching parameters, the evaluation produces:
- detected and missed leaks;
- the detection delay distribution (median, p90);
- the onset error, when `suspected_onset` is present;
- false alarms per week;
- a per-leak table.

It renders a report that always carries the "simulated data (BattLeDIM L-Town); not validated on a real network" header. When the run used mocks or provisional sensor IDs, the report carries the "NOT A RESULT" watermark. The matching logic is a pure function with no I/O.

**Blocked by:** 08

**Status:** ready-for-agent

- [ ] Tests with hand-made tables: alert before start, inside the window, after Tmax; two alerts for one leak; alert with no leak; a leak active at year start; `suspected_onset` null
- [ ] Tests: the header is always present; the watermark appears for mock or provisional runs
- [ ] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
