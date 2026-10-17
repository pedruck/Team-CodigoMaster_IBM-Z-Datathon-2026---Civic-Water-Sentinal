# 07: UI-007 — Demo runbook and contingency rehearsal (checkpoint C5)

**What to build:** A short demo runbook covers:
- how to start the dashboard against the live VM API (tunnel) and against a recorded run;
- the timed script;
- what to say for each banner (SIMULATED, RECORDED REPLAY);
- the contingency table from the spec (VM down, GRU fails, no real model, LLM down, audit down).

The demo is rehearsed twice, once with a forced fallback to playback. If EV-003 produced a real recorded run, the demo uses it; otherwise it uses the fixture run, still with the banner.

**Blocked by:** 04, 05

**Status:** in progress (presenter rehearsal pending)

- [x] Runbook committed; it never presents playback or mocks as live detection
- [ ] Two rehearsals recorded in the ticket's Comments (date, duration, what failed)
- [x] `docs/status.md` updated with the demo state

Spec: `.scratch/p6-dashboard-evaluation/spec.md`

## Comments

- 2026-10-17 19:24 — Technical rehearsal 1 (agent, live): tunnel to the VM API on local port 8800
  (8000 was taken by another local process), `streamlit run` up in 2 s, headless render 3.7 s:
  API online, simulated time 2019-12-31 23:55 UTC, mock-persistence/mock-threshold, SIMULATED,
  0 alerts. Total 5 s. Issue found: the empty state was shown twice (list and history) → history
  text changed to "No alerts match the history filters".
- 2026-10-17 19:24 — Technical rehearsal 2 (agent, forced fallback): tunnel killed → header "API
  unavailable (…)" and list "API unavailable since 22:24 UTC … retrying in 10 s"; switched to
  `fixture-cusum` → RECORDED REPLAY banner, alert R-SYN-A with detail. Total 4 s. Nothing failed.
- Fact-check of the runbook (subagent) fixed: fail-closed audit wording, condition before saying
  "no alert", the recording's mock forecaster and synthetic day, both "API unavailable" texts,
  VM address replaced by the `linuxone` alias, explicit ports.
- Pending: the spoken rehearsal with the presenter, twice (one with a forced fallback); record
  date, duration and what failed here.
