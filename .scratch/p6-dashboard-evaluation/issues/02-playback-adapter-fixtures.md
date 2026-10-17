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

**Status:** done (2026-10-17)

- [x] Configuration switches between HTTP and playback with no code change
- [x] Playback filtering, ordering and pagination match the API semantics (tests against the same cases as the API tests)
- [x] Every fixture file (except the deliberately malformed one) is parsed by the contract models in pytest
- [x] A headless app test shows the RECORDED REPLAY banner whenever playback is active
- [x] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`

## Comments

- 2026-10-17 — Done. Playback list semantics are tested against the API's own `AlertStore`
  (9 queries). Fixture runs come from the mock backend on a synthetic day with the official
  IDs; edge-case variants (IDs -91..-93) carry invented values and say so in `run.json`.
- Code review (two axes) fixes applied before commit: file-read failures → `SourceError`;
  `run.json` optional (evaluation output = alerts + status, story 48); the API metric reads
  "recorded replay" in playback; shipped `fixture-malformed`; `AlertQuery` bounds; script
  style; CONTRIBUTING exception for the fixtures.
- Deferred: the app-level "contract mismatch" test on `fixture-malformed` lands with UI-003
  (the page does not list alerts yet). Debt: files are re-read on every call (fine at fixture
  size, measure with an EV-003 run); regeneration rewrites `created_at`/`recorded_at`;
  write helpers duplicated between the script and the tests.
