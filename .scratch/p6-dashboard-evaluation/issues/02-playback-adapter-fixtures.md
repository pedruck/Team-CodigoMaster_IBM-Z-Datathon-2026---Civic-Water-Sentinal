# 02: UI-002 — Playback adapter and contract fixtures

**What to build:** The dashboard can be pointed, by configuration only, at a **recorded run** instead of a live backend. A recorded run is a JSONL file of alerts plus a status snapshot, in exactly the contract shape. The playback adapter applies the same filtering (`since`/`until` inclusive on `window_end`, `region`), newest-first ordering and `limit`/`offset` pagination as the API. While it is active, a permanent **"RECORDED REPLAY — run <id>, <date>"** banner is shown.

The fixture runs double as test data and demo rehearsal material. They cover:
- normal alerts;
- every nullable field null (`region`, `suspected_onset`, `hours_since_onset`, `cusum`, `forecast_contributions_m`, `summary`, `audit`);
- an empty deviating-sensor list;
- `is_mock=true`;
- an empty run;
- one malformed record, which must produce "contract mismatch".

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] Configuration switches between HTTP and playback with no code change
- [ ] Playback filtering, ordering and pagination match the API semantics (tests against the same cases as the API tests)
- [ ] Every fixture file (except the deliberately malformed one) is parsed by the contract models in pytest
- [ ] A headless app test shows the RECORDED REPLAY banner whenever playback is active
- [ ] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
