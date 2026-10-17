# 05: UI-005 — Polling, failure states and paginated history

**What to build:** The list refreshes about every 5 s (configurable) by asking for alerts since the newest `window_end` already shown. `since` is inclusive, so results are deduplicated by `alert_id` and an alert never appears twice.
- When the backend stops answering, a banner "API unavailable since hh:mm" appears and retries back off exponentially up to 60 s.
- A response that fails contract parsing shows the "contract mismatch" state instead of data.

The operator can also browse a **paginated history** (`limit`/`offset`) filtered by time range and region. `until`-anchored pagination is recorded as technical debt in the ticket and in `docs/status.md`.

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] Test: two consecutive refreshes with overlapping data yield no duplicates
- [ ] Test: an unreachable HTTP backend yields the unavailable banner; the back-off schedule is capped at 60 s
- [ ] Test: a malformed playback record yields the contract-mismatch state
- [ ] Test: history pagination and filters (time range, region) match API semantics
- [ ] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
