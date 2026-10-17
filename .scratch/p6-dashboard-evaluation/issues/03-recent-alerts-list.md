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

**Status:** done (2026-10-17)

- [x] Headless tests with playback fixtures: all-null alert renders, empty deviating list renders, empty run shows the empty state
- [x] Age uses the status' last timestamp, never the wall clock (test with a fixed status snapshot)
- [x] SIMULATED badge per mock alert
- [x] Headless test: `fixture-malformed` shows the "contract mismatch" state (carried over from UI-002)
- [x] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`

## Comments

- 2026-10-17 — Done. Tests through the headless app on playback runs (and one through the HTTP
  adapter against a stub); expected values are literals from the fixtures (e.g. age
  "7 h 15 min" = status 23:55 − alert 16:40).
- TDD note: the empty and contract-mismatch states were implemented in the first cycle (ahead
  of their tests); their tests were checked by a deliberate mutation (they fail when the code
  is broken).
- Code review fixes: null z → "not provided"; empty sensor list → "no deviating sensor";
  age after the last window is flagged instead of clamped; ordering tested with distinct
  `window_end`; malformed test pinned to the alerts message; list through the HTTP adapter.
- Debt: the loading spinner is not asserted (AppTest does not expose it reliably); date format
  duplicated with `header.py`; AppTest setup duplicated across three test files; table rows are
  `dict` rather than a typed row.
