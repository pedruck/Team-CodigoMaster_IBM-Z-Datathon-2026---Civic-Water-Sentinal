# 03: UI-003 — "Recent alerts" list

**What to build:** The operator sees **"Recent alerts"** (not "active": the contract never closes alerts), newest `window_end` first.

Each row shows:
- the window end;
- the region, or "region not available";
- the number of deviating sensors;
- the strongest deviation (m) and its z;
- the estimated onset, or "not provided";
- the alert's age, computed as simulated now minus `window_end`;
- a SIMULATED badge when `is_mock`.

There is a loading state on first load and an empty state "no alerts in this period". It works with both adapters.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Headless tests with playback fixtures: all-null alert renders, empty deviating list renders, empty run shows the empty state
- [ ] Age uses the status' last timestamp, never the wall clock (test with a fixed status snapshot)
- [ ] SIMULATED badge per mock alert
- [ ] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
