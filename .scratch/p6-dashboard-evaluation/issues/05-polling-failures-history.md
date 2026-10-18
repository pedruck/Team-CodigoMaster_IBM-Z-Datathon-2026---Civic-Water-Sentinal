# 05: UI-005 — Polling, failure states and paginated history

**What to build:** The list refreshes about every 5 s (configurable) by asking for alerts since the newest `window_end` already shown. `since` is inclusive, so results are deduplicated by `alert_id` and an alert never appears twice.
- When the backend stops answering, a banner "API unavailable since hh:mm" appears and retries back off exponentially up to 60 s.
- A response that fails contract parsing shows the "contract mismatch" state instead of data.

The operator can also browse a **paginated history** (`limit`/`offset`) filtered by time range and region. `until`-anchored pagination is recorded as technical debt in the ticket and in `docs/status.md`.

**Blocked by:** 03

**Status:** done (2026-10-17)

- [x] Test: two consecutive refreshes with overlapping data yield no duplicates
- [x] Test: an unreachable HTTP backend yields the unavailable banner; the back-off schedule is capped at 60 s
- [x] Test: a malformed playback record yields the contract-mismatch state
- [x] Test: history pagination and filters (time range, region) match API semantics
- [x] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`

## Comments

- 2026-10-17 — Done by a worktree subagent (refresh: 19 tests, history: 19 tests), integrated
  by cherry-pick with a hand-resolved conflict in `app.py`.
- Review fixes: full rerun when the simulated time or the alerts change (header clock and
  detail selector follow the list); contract mismatch clears the shown alerts; warning when
  more than 500 new alerts arrive in one poll.
- Debt: `until`-anchored pagination; history does not auto-refresh nor reset the page on a
  filter change; back-off applies to every error kind; three requests per tick; test helpers
  (stub, fixture writers, app launch) duplicated across test files; error wording repeated in
  three modules.
