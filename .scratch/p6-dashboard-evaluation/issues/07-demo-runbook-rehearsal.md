# 07: UI-007 — Demo runbook and contingency rehearsal (checkpoint C5)

**What to build:** A short demo runbook covers:
- how to start the dashboard against the live VM API (tunnel) and against a recorded run;
- the timed script;
- what to say for each banner (SIMULATED, RECORDED REPLAY);
- the contingency table from the spec (VM down, GRU fails, no real model, LLM down, audit down).

The demo is rehearsed twice, once with a forced fallback to playback. If EV-003 produced a real recorded run, the demo uses it; otherwise it uses the fixture run, still with the banner.

**Blocked by:** 04, 05

**Status:** ready-for-agent

- [ ] Runbook committed; it never presents playback or mocks as live detection
- [ ] Two rehearsals recorded in the ticket's Comments (date, duration, what failed)
- [ ] `docs/status.md` updated with the demo state

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
