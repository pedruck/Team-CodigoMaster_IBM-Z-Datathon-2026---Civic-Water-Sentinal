# Project status — Backend & Ingestion

**Last update:** 2026-10-18 — branch `feat/be-012h-final-calibration-v1` (detection owner): v1 of the final calibration is committed. `config/calibration.xgboost-33.json` (sha256 `fad6ec0b…`, `provenance = battledim-2018-oof`, forecaster `xgboost-33@0.1.0` with neighbours, budget 1 false alarm per week as adopted by the detection owner, still open to the team in Q-TEAM-03) makes `SENTINEL_DETECTOR=cusum` start with `models.detector.is_mock = false` for the model the shared API serves; the better variant without neighbours is the documented upgrade once P2 exports it (`docs/p4-calibration.md` §7). The shared API on the VM is **not** restarted by this change. BE-012f is closed (merged from `feat/be-012f-dev-calibration`): the development run (`offline/devcal.py`, artefact `dev-baseline-2018`, `is_mock`) reproduces byte for byte from data v1, and the detection owner's review of ADR-010 is appended to it. Before: branch `feat/be-012j-calibration-experiment` (detection owner): the CUSUM calibration experiment on the ML owner's 2018 out-of-fold residuals (BE-012j tools, BE-012h run, `docs/p4-calibration.md`). Pre-registered in two commits (`ae93951` exploration rules before any residual was read, `6c0be6c` sweep rules before any replay). Both XGBoost variants pass the persistence test; at the proposed budget of 1 false alarm per week (ADR-010 operator view) the served model (with neighbours) detects 8/14 leaks (abrupt 6/6, median delay 4.4 h) and the variant without neighbours 12/14 (abrupt 6/6, 1.5 h); the incipient counts are at chance level (post-hoc placebo). Candidate artefacts `battledim-2018-oof` exist outside git; the final one waits for Q-TEAM-03 and Q-ML-07. Before: branch `feat/p2-oof-residuals` (ML owner, PR #19, merged): the out-of-fold XGBoost residual generator for DEP-DET-05 (both Q-ML-07 variants; run on the VM with the real data, files in `data/v1/oof/xgboost-33@0.1.0/`: MAE on clean windows 6.7 mm with neighbours vs 16.1 mm without, leak contrast 4.71 vs 5.62), the optional `ml` extra (DEP-ML-03) and TreeSHAP only for the sensors of an alert (`SENTINEL_FORECASTER_CONTRIBUTIONS=on_alert`, new default; optional `ExplainingForecaster`, proposed to P3). Before: the real L-Town sensor → region mapping and a candidate pipe per alert exist (BE-012g, BE-012i, ADR-011): `config/regions.battledim.json`, built by a rule fixed from the topology alone from the data owner's `network/v1/l-town.json` (the pipe network the detector reads, outside git); `Alert` is contract 1.1 (optional `candidate_pipe`). Before: branch `zanetti/be-011-forecaster-wiring` (PR #7, `main` merged in): BE-011, the API loads the real forecaster at startup (`SENTINEL_FORECASTER=xgboost`); full 2019 in process on s390x with XGBoost. Before, on `main`: the CUSUM detector (BE-012a..e) is selectable at startup (`SENTINEL_DETECTOR=cusum` + `SENTINEL_DETECTOR_CALIBRATION`, optional `SENTINEL_DETECTOR_REGIONS`) and was checked once against the real server with synthetic data; the offline tools of BE-012f exist and are tested. Nothing is calibrated on real residuals yet: every CUSUM output is `is_mock=true`, and no calibration matches `xgboost-33@0.1.0` (DEP-DET-05). `feat/p2-forecasting` delivered the real forecaster (XGBoost per sensor, Snap ML) and measured it on the VM.

**Dashboard update:** 2026-10-18 — branch `feat/be-021-dashboard-redesign` (PR #18): BE-023 repairs sidebar collapse, typography, heading margins and responsive controls. Seven Chrome viewport widths and enlarged text checked; 894 tests passed, 39 skipped. BE-022's map source hash matches the prompt; the exported JSON digest discrepancy and exact Figma frame review remain documented below.

## Summary

**Responsive UI repair (BE-023, 2026-10-18):** collapsed navigation no longer reserves
270 px on tablets. Sidebar controls have visible contrast, spacing is consistent and all
footer/source information remains reachable. The native theme uses the locally bundled
fonts, headings no longer retain negative native margins, buttons align with their content
columns and long labels reflow. Network-map navigation updates its current state and closes
the mobile drawer after an explicit selection. See `docs/dashboard-design.md` for the
seven-viewport regression check and its exact commands.

**Network map and visual refresh (BE-022, 2026-10-18):** the operator page now follows the
public CivicWater Figma colour/type direction and places the official L-Town schematic
with selected-alert evidence after the issued-alert feed. The map is a derived, attributed
JSON/SVG asset (909 links, 33 sensors, three landmarks), with a separate Streamlit view,
Jupyter slider and SVG export. The original records, disclosures, filters, polling and
simulation notices remain. A local hand-authored mock alert demonstrates Z1-R3/p4/n163/n188
without presenting it as detector output. Exact frame comparison with Figma remains pending
because the full design URL returned 403. See ADR-012 and `docs/dashboard-design.md`.

**Dashboard presentation (BE-021, 2026-10-17):** the primary Streamlit page now has a
responsive workspace with issued alerts first, 16/14 px alert text, full records on demand
and expandable technical status. Local assets, source-derived diagrams and manual refresh
remain; exact evidence, mandatory notices and history filters are retained. Full gates:
**741 passed, 34 skipped**, ruff and strict mypy green. Five Chrome viewport sizes,
reduced motion, interactions, keyboard focus, primary-feed contrast and 200% text checks
passed; no external requests or browser errors. See `docs/dashboard-design.md` and the
requested skill review in `docs/dashboard-ux-review.md`. Owner visual validation pending.

The backend runs end to end **with mocks**: windows are validated, ordered, buffered,
passed to a mock forecaster and a mock detector, alerts are stored and served, audit events
are emitted to a dev sink, and per-stage latency is measured. Nothing is integrated with the
real models or the real audit log yet. The real forecaster (XGBoost, chosen by the ML owner; `docs/p2-forecasting.md`) is selected with `SENTINEL_FORECASTER=xgboost` + `SENTINEL_FORECASTER_PATH` and loaded at startup (BE-011); the default is still the mock, and the detector is still the mock, which raises **no** alerts with XGBoost residuals (σ 1 m vs millimetre residuals, `docs/performance.md`). The official sensor set is read from the data owner's
`config/sensors.battledim.json` (official registry, data owner), and the whole 2019 replay passes
validation with 0 rejections. On the LinuxONE VM (s390x) the
runtime installs from wheels and the 89 tests pass (recorded before the detector work); mypy does not install there yet.
`CusumDetector` (per-sensor episodes, gap decay, atomic rejection, calibration artefact with hash,
region ranking, candidate pipe) exists and is tested with **invented, synthetic** calibrations.
The L-Town regions (nine, over the 33 pressure sensors) are committed and the pipe network (905
pipes) is the data owner's `l-town.json` (`SENTINEL_DETECTOR_REGIONS`, `SENTINEL_DETECTOR_NETWORK`); the candidate pipe is a
topological guess (ADR-011), measured only on 2018 with a development calibration. It is selected with
`SENTINEL_DETECTOR=cusum` and `SENTINEL_DETECTOR_CALIBRATION` (required; `SENTINEL_DETECTOR_REGIONS`
optional); the server refuses to start with a `ConfigError` on a missing or invalid file, another
forecaster, other or reordered sensors or a mapping that does not cover the registry, and `mock`
stays the default (README). A manual end-to-end run against the real `uvicorn` server with
synthetic data found no bug (`docs/testing.md`). Nothing is calibrated on real residuals
(DEP-DET-05) and any output of the CUSUM is `is_mock=true`. **Update 2026-10-18:** DEP-DET-05 was received and the calibration experiment ran (BE-012j/h, `docs/p4-calibration.md`); the calibration for the served model is committed (`config/calibration.xgboost-33.json`, v1; the one for the variant without neighbours stays outside git until that model is exported). The offline tools of BE-012f live in
`src/sentinel/detection/offline/` (dev only, never imported by the server). All contracts are v1.0
`Proposed` until the other owners confirm them.

## Done (tests green)

BE-023 sidebar/responsive UI repair (2026-10-18): seven widths from 360 to 1920 px,
open/collapsed navigation, evidence/history interactions and 200% text checked in Chrome;
optional browser regression script committed.

BE-021 Streamlit operator workspace redesign (2026-10-17): presentation-only diagrams,
manual refresh, alerts-first readability refinement, thirteen new presentation/interaction
tests, UX review and desktop/mobile captures.

BE-001 structure/tooling · BE-002 contracts + examples · BE-003 validation · BE-004 history ·
BE-005 ports + mocks · BE-006 pipeline · BE-007 HTTP API · BE-008 synthetic replay ·
BE-009 metrics/logs/benchmark (x86, mocks) · BE-010 real sensor registry · BE-018 CI (GitHub Actions) · BE-019 fail-closed audit + z sign
convention · BE-014 full 2019 HTTP replay (mocks) · BE-020 shared API script + runbook ·
BE-011 real forecaster wired (`SENTINEL_FORECASTER`) ·
BE-012a..e CUSUM detector: episodes, gaps, atomic rejection, calibration artefact, `calibrate()`,
`for_pipeline`, region ranking · BE-012 selection `SENTINEL_DETECTOR=cusum` in `build_pipeline`
(startup refusals as `ConfigError`, `mock` default; `tests/test_detector_startup.py`) ·
BE-012g real L-Town sensor → region mapping and BE-012i candidate pipe per alert (ADR-011,
`Alert` 1.1, `SENTINEL_DETECTOR_NETWORK`) ·
BE-012f: out-of-fold hour-of-week residuals (`offline/baseline.py`), replay with
exact parity with the detector and parameter sweep (`offline/sweep.py`), layer-1 counts with a
**provisional** matching rule (`offline/layer1.py`, Q-UI-06) and the development run
(`offline/devcal.py`, artefact `dev-baseline-2018`, `is_mock`, reproduced byte for byte) · BE-012j calibration experiment: OOF reader, persistence and δ, `trajectory`, selection with leave-one-leak-out, recovery, two-phase command line and protocol (`offline/{oof,persistence,selection,experiment}.py`). Details:
`docs/implementation-plan.md`.

## In progress

- BE-022: network map and visual refresh implemented and tested; owner visual review,
  full Figma frame comparison (URL returned 403) and the exported-map digest discrepancy
  remain review items (ADR-012).
- BE-012 umbrella: selection, tools, regions and candidate pipe done; BE-012h (final calibration
  and layer-1 evaluation) IN_PROGRESS: the run on both XGBoost variants is done
  (`docs/p4-calibration.md`) and the v1 artefact for the served model is committed
  (`config/calibration.xgboost-33.json`). Missing: the team's confirmation of the budget
  (Q-TEAM-03), the variant without neighbours (needs an exported model, ML owner) and the layer-1
  evaluation with the final artefact.
- BE-012f: DONE. Development calibration on 2018 (out-of-fold hour-of-week baseline,
  `provenance = dev-baseline-2018`, `is_mock = true`): tools and `offline/devcal.py`, reproduced byte for byte from data v1.
- BE-015 LinuxONE: mock baseline, parity hash and HTTP replay done; real forecaster measured in
  process (full 2019); full `just check` waits for Q-PLAT-02 (`gcc`). The detector
  determinism digest `d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413` (`tests/detection/test_determinism.py`) is the same on x86-64 and on s390x (passed on the VM, 2026-10-17, PR #7 integration check). BE-012i added the `candidate_pipe` key to the alert JSON, so the literal is now `7c5ef97bf8ccee4e19695475142658b426be29f796298314ab054a277ad3eee3` (the replay without that key gives `d9106c15e4d5…`); **its s390x run is pending**.

## Blocked

| Task | Waiting for |
|---|---|
| BE-012h upgrade to the variant without neighbours (v1 is committed) | Q-ML-07 (export of that model, ML owner); Q-TEAM-03 (team confirms the budget) |
| BE-013 real audit sink | DEP-AUD-01 (audit owner) |

## Pending (independent, not started)

BE-016 dashboard needs (after Q-UI answers) · BE-017 load test with keep-alive client.

Small leftovers of the CUSUM selection, outside the scope of the documentation pass that
recorded them: the comment above `SENTINEL_FORECASTER` in `.env.example` still says that only
`mock` exists and the file does not list the new detector variables; `scripts/bench_pipeline.py`
builds its `Settings` without the environment, so `just bench` cannot measure the CUSUM;
`scripts/shared_api.sh` does not forward `SENTINEL_DETECTOR*`; a refused startup prints a Python
traceback whose last line is the `ConfigError` (for pydantic errors, a documentation URL).

P6 dashboard & evaluation: UI-001 DONE (dashboard tracer), UI-002 DONE (playback + fixtures), UI-003..006 DONE (recent alerts, detail, polling + history, cross-team proposals + ADR-009); UI-007 IN_PROGRESS (runbook `docs/demo-runbook.md`, two technical rehearsals OK on 2026-10-17: live via tunnel and forced fallback to `fixture-cusum`; presenter rehearsal pending); EV-001 IN_PROGRESS (ADR-010 `Proposed`, awaiting P4 review); EV-002 DONE (metrics + report, `src/sentinel/eval/`); EV-003 IN_PROGRESS (recorder + CLI `python -m sentinel.eval` done and tested; the 2019 run waits for the final calibration and for Q-DATA-07: the real `leaks_2019.csv` clips the 4 inherited leaks to 2019-01-01, found by the official-list check), spec and tickets in
`.scratch/p6-dashboard-evaluation/`; EV-003 gated on the real detector by 2026-10-17 22:00.

## External dependencies

15 open/partial items in `docs/dependencies.md` (DEP-DET-04 resolved by BE-012g; DEP-DET-06 partial:
candidate pipe done, the official distance is the evaluation owner's); P0 questions in
`docs/questions-for-team.md` (Q-TEAM-01/02, Q-ML-01/02/08, Q-DET-01,
Q-AUD-01/03); Q-PLAT-01 answered, Q-PLAT-02 (P1) open.

## Risks

- The candidate pipe (ADR-011 §3) is a topological guess, not a hydraulic localisation. On the 6
  abrupt 2018 leaks, with a development calibration and its first alert, it was within about 360 m
  of the leaking pipe five times and 2.9 km once; that is six events, not a result, and it is
  untested on incipient leaks. The region rule's diameter (1000 m) is a choice; the sensitivity
  table is in ADR-011.
- `Alert` 1.1 (new optional key, `schema_version: "1.1"`) and the `alert.raised` audit payload
  change shape for the backend, audit and dashboard owners, who have not confirmed it yet
  (ADR-011 is `Proposed`; Q-DET-08 asks them). The determinism digest of the detector was regenerated for the new
  key (`tests/detection/test_determinism.py`); s390x still has to reproduce the new literal.
- The server reads the pipe network from the data owner's `l-town.json` in
  `$SENTINEL_DATA_DIR/network/v1/` (outside git). `SENTINEL_DETECTOR_NETWORK` is optional; without
  it `candidate_pipe` is null. On the LinuxONE VM this was **not run** (no access from the author's
  session): `docs/runbook-vm.md` has the commands to verify it there (`topology --check` and the tests that read the file).
- Unmerged owner branches change the P6 plan (2026-10-17 18:30): `feat/be-012-cusum-detector`
  (real CUSUM, ADR-007/008: layer-2 2019 evaluation through the API is P6's, Q-UI-06) and
  `leo/local-version` (hash chain, verifier CLI, narrative; no HTTP endpoint, records fail-open,
  Q-DOC-01). P6 renumbered to ADR-009/010, Q-UI-07, Q-DET-07.

- The shared API on port 8000 runs `main` @ `333b1bd` with the **real XGBoost forecaster**
  (`xgboost-33@0.1.0`, TreeSHAP off, `compress_trees`) and the **mock detector**, and holds 2019
  in memory (0 alerts: the mock's σ = 1 m against millimetre residuals). Any restart wipes it
  (re-run the replay, `docs/runbook-vm.md`).
- Separate repository may need to be merged into a team repository (Q-TEAM-02).
- Gap policy blinds scoring for 12 h after any missing window (ADR-004); v1 has no gaps.
- Fail-closed audit: during an audit outage no new alerts are served; the pending queue is in
  memory and lost on restart (ADR-006).
- In-memory state: restart loses history, results, alerts, metrics, detector state (ADR-003).
- Detector: the 2018 calibration exists only as candidates outside git (`docs/p4-calibration.md`); the
  served model (with neighbours) detects abrupt leaks later (median 4.4 h) than the variant without
  neighbours (1.5 h), which has no exported model yet. Incipient detections in the 7-day view are at
  chance level at about one false alarm per week (placebo): never present them as detections. 14
  events: counts only; leave-one-leak-out shows the selection's optimism. The false-alarm count of a sweep is not monotone in `h` (ADR-008 §E), so
  a sweep table must not be interpolated, and the layer-1 matching rule is provisional (Q-UI-06):
  any delay or false-alarm count computed with it is provisional too. With the persistence mock
  forecaster a step does not accumulate (a ramp does), so the served CUSUM is only a mechanics
  check until the real forecaster arrives (ADR-007 §1). A new CUSUM episode on a sensor whose
  `S` has not returned to 0 inherits the old onset (Q-DET-06). If the pipeline discards a result
  the detector already returned, its state has advanced while the window was lost (ADR-007
  Risks).
- `mypy` not installable on s390x without `gcc` (Q-PLAT-02); model runtime wheels on s390x unverified. Model runtime (numpy, xgboost, snapml) comes from the VM image's `/opt/venv`, not `uv`; it is lost when the container is recreated (DEP-ML-03).
- With TreeSHAP contributions the forecaster takes ~134 ms per window: ≈ 3.9 h for a full-year replay (estimate; ≈ 67 s without contributions, `docs/performance.md`).

## Known bottlenecks

With mocks on x86, HTTP handling (p50 ≈ 4 ms server-side) dominates the pipeline
(p50 ≈ 0.4–0.8 ms). Both are far below the 300 s interval. With the CUSUM selected (synthetic
calibration, mock forecaster, x86) the `detection` stage had p50 about 0.1 ms in process and
0.29 ms over HTTP, single measurements (`docs/performance.md`).

## Future optimisations

Only with evidence from LinuxONE + real models: profile `content_hash`, keep-alive replay
client, out-of-process model adapter if a runtime cannot be imported (ADR-005).

## Pending for the detection owner

- Pre-registration of ADR-008 §G: the region rule and the files, δ (→ `k_N`) and the persistence
  criterion are **registered** (ADR-011 §2; `config/calibration-protocol.xgboost-33.json`). The artefact hash is
  **registered** (v1, `fad6ec0b…`) and the budget is adopted at 1 per week (the team can still change
  it, Q-TEAM-03). Still `not yet registered`: the matching rule (Q-UI-06). Must be committed
  before any evaluation run.
- Open questions: Q-ML-07/08 (ML), Q-UI-05/06 (dashboard/evaluation), Q-TEAM-03, Q-DET-05,
  Q-DET-06 (onset inherited by a new episode). Dependencies DEP-DET-05 (and DEP-DET-06: the
  official BattLeDIM distance, evaluation owner).
- ADR-007/008 are `Proposed`: they need the ML owner (Q-ML-07), the dashboard owner (Q-UI-05)
  and the evaluation owner (Q-UI-06). ADR-011 is `Proposed`: it needs the backend owner (`Alert`
  1.1), the evaluation owner (ADR-010 §6–7: pipe → region table, official distance) and the data
  owner (the graph).

## Next recommended step

Start the shared API with `SENTINEL_DETECTOR=cusum` and `config/calibration.xgboost-33.json` (runbook, announce
first) so the integration shows a real detector; the team confirms the budget (Q-TEAM-03) and P2 exports the variant without
neighbours (Q-ML-07), which is the planned upgrade; P6 can run 2019 (EV-003); then the real audit sink (BE-013) and p50/p99 with real models (BE-015, incl. the
already passed on s390x). Independent: BE-017 (keep-alive load test).

## Verification (2026-10-18 and before)

| Command | Result |
|---|---|
| BE-023: `python -m ruff format src tests scripts dashboard`, `python -m ruff check src tests scripts dashboard`, `python -m mypy`, `python -m pytest -q -o cache_dir=E:/Codex/civicwater-sentinel/pytest-cache --basetemp=E:/Codex/civicwater-sentinel/pytest-tmp` | **164 files unchanged**, lint OK, **163 source files** with no mypy issues; **894 passed, 39 skipped in 40.23s** (external data/optional ML dependencies absent) |
| BE-023: `node scripts/check_dashboard_responsive.cjs http://127.0.0.1:8501 E:/Codex/civicwater-sentinel/ui-check`; existing focused accessibility/map scripts (`docs/dashboard-design.md`) | Passed at **360, 390, 768, 1024, 1280, 1440 and 1920 px**, 900 px height: sidebar open/collapse and restored main width, current map section/mobile dismissal, evidence selection, raw audit disclosure, history filtering/reset, local typography, 44 px main targets and 200% text without page/sidebar horizontal overflow. Primary-feed contrast ≥5.27:1; keyboard focus retained through polling. Map page/navigation/SVG control rechecked. No browser page errors |
| BE-022 integration with `origin/main` at `8aed1b9`: same four Python gate commands and browser map check below | **164 files formatted**, lint OK, **163 source files** with no mypy issues; **894 passed, 39 skipped in 40.38s**. Changelog/status conflicts resolved by retaining both changes; desktop/mobile map, navigation and download rechecked successfully |
| BE-022, Windows x86-64 / Python 3.12.14: `python -m ruff format --check src tests scripts dashboard`, `python -m ruff check src tests scripts dashboard`, `python -m mypy`, `python -m pytest -q` (same environment/cache paths as BE-021) | **145 files formatted**, lint OK, **144 source files** with no mypy issues; **753 passed, 34 skipped in 39.12s**. Skips require external data or optional ML dependencies |
| BE-022, `node E:\Codex\civicwater-sentinel\browser\validate-map.cjs`, Chrome at 1536×1080 and 390×844 | Map image and IBM Plex fonts loaded, no browser errors or horizontal page overflow; internal mobile map scrolling, full-map navigation and SVG download checked. Captures use an explicitly hand-authored synthetic alert, not detector output |
| BE-022 source reproduction: `sentinel.data.build_network` then `sentinel.data.export_map` from official L-TOWN.inp | Input SHA-256 `a7551b86745f4cc3433c78e60023077fa1386947d4d35372ffd3a0405622b436` matches prompt. Exported JSON `e165e9fdb15521af7673e0c2f3f2e764382ae5547e8e32c25f9a6e721472e9a4` differs from prompt's expected `2930fd5d016bb277bf718d03fb880d4e2eece22984500b14ff8227b7fbf6b30e`; 909 links, 905 pipes, 33 sensors and 3 landmarks. Discrepancy remains documented for review |
| BE-021, Windows x86-64 / Python 3.12.14: `python -m ruff format --check src tests scripts dashboard`, `python -m ruff check src tests scripts dashboard`, `python -m mypy`, `python -m pytest -q` (cache/temp redirected to E; exact environment in `docs/dashboard-design.md`) | **138 files formatted**, lint OK, **137 files** with no mypy issues; **741 passed, 34 skipped in 42.36s** (optional data/ML dependencies absent). Includes thirteen new presentation/interaction tests. Fixed two existing Windows-only guard failures: pinned JSON checkout line endings and POSIX-normalised path exclusion |
| BE-021, Playwright + installed Chrome: 1536×1080, 390×844, 360×800, 768×1024, 1440×900 with reduced motion; evidence navigation, raw audit/sensor disclosures, history filter/reset and manual refresh | Passed; no page errors, no horizontal page overflow, all local font/icon assets loaded, **0 external requests**, **0 GSAP tweens** with reduced motion. Captures: `docs/images/dashboard-*.png` |
| BE-021 UX review: primary-feed contrast including opacity, evidence activation/focus, focus retained through polling, heading order, button height and 200% root text at 1536/390 px | Passed focused browser checks: warning 8.12:1, sampled feed text ≥5.03:1, caption 5.90:1; evidence button 44 px high; no horizontal page overflow with enlarged text. Full WCAG conformance and spoken screen-reader output not assessed |
| branch `feat/be-012h-final-calibration-v1` (#20 and #16 merged in; x86-64, CPython 3.14.7): `ruff format --check`, `ruff check` (src tests scripts dashboard), `uv run --group dashboard mypy`, `uv run --group dashboard pytest -q` | 167 files formatted · ruff OK · mypy: no issues in 167 files · **1027 passed, 40 skipped** |
| same pytest with `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data` | **1051 passed, 16 skipped** |
| `tests/detection/test_committed_calibration.py` | 6 passed; `sha256sum config/calibration.xgboost-33.json` = the hash of ADR-008 §G |
| s390x / start on the VM | NOT_RUN (the shared API was not restarted) |
| branch `feat/be-012j-calibration-experiment` (x86-64, CPython 3.14.7): `ruff format --check`, `ruff check` (src tests scripts dashboard), `uv run --group dashboard mypy`, `uv run --group dashboard pytest -q` | 147 files formatted · ruff OK · mypy: no issues in 147 files · **822 passed, 39 skipped** |
| same, with `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data` | **845 passed, 16 skipped** |
| `tests/detection/test_determinism.py` (detector unchanged) | 2 passed, same digest |
| DEP-DET-05 files copied from the VM: `zcat residuals_long.csv.gz \| sha256sum`, `sha256sum folds.json` (both variants) | `4878bb7c…` / `a7f46f42…` / `3e04574f…`, equal to `declaration.json` and to the protocol |
| `python -m sentinel.detection.offline.experiment explore` (both variants), after commit `ae93951` | persistence PASS (median 0.957 / 0.962, minimum 0.539 / 0.434); `explore.json` `293c2a99…` / `b958461a…` |
| `... experiment sweep --provenance battledim-2018-oof` (both variants), after commit `6c0be6c`, then again at `ca2ca8f` | 95 cells each; 1 per week: 8/14 at 0.84 (with) and 12/14 at 0.997 (without); the 16 output files identical between the two runs; candidate artefacts `fad6ec0b…` / `d289eba6…` |
| BE-012f (2026-10-18): `uv run ruff format --check src tests`, `uv run ruff check src tests`, `uv run pytest -q` | 121 files formatted · all checks passed · **735 passed, 48 skipped**; `mypy` NOT_RUN to completion: its only errors are `import-not-found` for `streamlit` in `dashboard/` (not installed in the fresh venv) |
| BE-012f reproduction: `python -m sentinel.detection.offline.devcal` with the report's parameters against data v1 | `sweep.csv`, `folds.json`, artefact (`7057b5c0…`) and residual-text sha256 (`489bd0bb…`) identical to the stored run; `report.md` differs only where the code now floors `diurnal_scale_ratio` (no `inf`) |
| Jupyter view: `tests/test_dashboard_jupyter.py` (7) + run of `JupyterDashboard` inside the VM's `notebook` container (s390x, `/opt/venv`, no install) against the shared API and `fixture-cusum` | live: online, 2019-12-31 23:55, mocks, no alerts; playback: RECORDED REPLAY, alert R-SYN-A with detail. Browser rendering of the widgets not checked |
| Merge of `main` (a63d367: CUSUM detector, XGBoost forecaster) into `feat/dashboard-mvp`; conflict markers left staged in `CHANGELOG.md`, `docs/adr/README.md`, `docs/dependencies.md` resolved | ruff OK · mypy: no issues in 112 files · **599 passed, 18 skipped** |
| Dashboard vs real CUSUM: `fixture-cusum` (`scripts/make_playback_fixtures.py`) + `tests/test_dashboard_cusum.py`; gates | mypy: no issues in 113 files · **604 passed, 18 skipped** |
| Scratch e2e (not committed): real `uvicorn` with `SENTINEL_DETECTOR=cusum` (invented synthetic calibration + 2-region mapping), 288 synthetic windows with a ramp on n1/n4/n31, dashboard `AppTest` over HTTP | 288 × 201; 1 alert `A-20190314T1705Z-1`, region R-SYN-A, onset 16:35, CUSUM 9.393 > 8.0, age 6 h 50 min, audit seq 205; SIMULATED shown; no exception |
| UI-004..006 integrated (three worktrees, cherry-picked; app.py conflict resolved) + review fixes: `ruff format --check`, `ruff check`, `mypy`, `pytest` | ruff OK · mypy: no issues in 81 files · **225 passed, 8 skipped** (P1 data tests) |
| UI-003 gates (x86, Python 3.12): `ruff format`, `ruff check`, `mypy`, `pytest` | ruff OK · mypy: no issues in 74 files · **169 passed, 8 skipped** (P1 data tests) |
| UI-002 gates (x86, Python 3.12): `ruff format`, `ruff check`, `mypy`, `pytest` | ruff OK · mypy: no issues in 72 files · **160 passed, 8 skipped** (P1 data tests) |
| UI-001 gates (x86, Python 3.12, `uv sync --locked --group dashboard`): `ruff format`, `ruff check`, `mypy`, `pytest` over src tests scripts dashboard | ruff OK · mypy: no issues in 70 files · **133 passed, 8 skipped** (P1 data tests, no `$SENTINEL_DATA_DIR`) |
| UI-001 smoke: SSH tunnel `-L 8000:172.17.0.2:8000 linuxone` (ssh-config alias; address in the team channel), dashboard `AppTest` + `streamlit run --server.headless` | header: API online, simulated time 2019-12-31 23:55 UTC, mock-persistence/mock-threshold, SIMULATED badge; `/_stcore/health` ok |
| `just check` (ruff format, ruff check, mypy strict, pytest) | 52 files unchanged · ruff: all checks passed · mypy: no issues in 52 files · **89 passed** |
| `just bench 5000` | 5000 windows, total p50 0.3707 ms / p99 0.7358 ms (mocks) |
| uvicorn + `scripts/replay.py` (576 synthetic windows) | 576 × 201; resend 5 × 200; 1 mock alert; client p50 6.797 / p99 14.837 ms |
| `just dev` + README curl + `just replay` (fresh server) | example → 201 `insufficient_history`; 288 × 201; 1 mock alert |
| GitHub Actions run 39040317019 (`just ci`, ubuntu-latest, Python 3.12) | success · 52 files already formatted · ruff OK · mypy OK · **89 passed** |
| LinuxONE VM (s390x, Python 3.12.3): `uv sync --locked --no-dev` + pinned `ruff format --check`, `ruff check`, `pytest` | runtime deps installed from wheels · 52 files formatted · ruff OK · **89 passed** |
| LinuxONE: `uv sync --locked` (with dev group) | **FAILED** — `librt` 0.16.0 (mypy dep) needs `gcc`, absent on the VM |
| LinuxONE: `scripts/bench_pipeline.py --windows 5000` | total p50 0.1645 ms / p99 0.3514 ms (mocks) |
| LinuxONE: uvicorn + `scripts/replay.py` (576 windows, localhost) | 576 × 201; resend 5 × 200; 1 mock alert; client p50 1.419 / p99 17.988 ms |
| x86 vs s390x `content_hash` of `window.valid.json` | identical (`7fc91e5a…9717fa`) |
| LinuxONE container (`/opt/venv`), branch `zanetti/ingestion` @ `073c605`: `python -m pytest -q` | **107 passed** |
| x86: `just check` @ `073c605` | ruff OK · mypy OK (57 files) · **107 passed** |
| LinuxONE: `scripts/validate_v1_ingestion.py $SENTINEL_DATA_DIR/v1 --year 2019` @ `073c605` (CSV timestamps sent as is) | 105 120 windows, 0 rejected, 105 120 with `timestamp_tz_assumed`, pipeline p50 0.2231 / p99 0.4074 ms |
| branch `zanetti/audit-fail-closed` @ `1251d95` — x86 `just ci` · LinuxONE `python -m pytest -q` · LinuxONE `validate_v1_ingestion.py` 2019 (in-memory audit) | ruff OK · mypy OK (58 files) · **115 passed** (x86 and s390x) · 105 120 windows, 0 rejected, p50 0.221 / p99 0.3156 ms |
| branch `zanetti/shared-api` @ `d4a2750` — x86 `just check` · LinuxONE `python -m pytest -q` (with `SENTINEL_DATA_DIR`) | ruff OK · mypy OK (63 files) · x86 121 passed + 8 skipped (P1 data) · **s390x 129 passed** |
| LinuxONE: `validate_replay_ingestion.py windows_2019.jsonl` (in process) | 105 120 windows, 0 rejected, 0 tz assumed, p50 0.2157 / p99 0.3026 ms |
| LinuxONE: `shared_api.sh start / start (dup) / stop / status / start` | started; duplicate refused (exit 1); stopped, process gone; restarted |
| LinuxONE: `scripts/replay.py windows_2019.jsonl` → shared API :8000 | 105 120 × 201, 723.78 windows/s, client p50 1.284 / p99 2.861 ms |
| local tunnel `-L 18000:172.17.0.2:8000` | `/health` ok, `/docs` 200 |
| branch `wip/be-012-docs` @ `a93883b` — x86-64 (Intel Core Ultra 7 155H), Python 3.14.7: `just check` | ruff format: 75 files unchanged · ruff check OK · mypy: no issues in 75 source files · **299 passed, 8 skipped** (the 8 skips are the P1 data tests without `SENTINEL_DATA_DIR`) |
| same, `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data uv run pytest tests/test_p1_data.py -q` | **14 passed** |
| same, `uv run pytest tests/detection -q` | **178 passed** |
| detector determinism digest, x86-64 (`tests/detection/test_determinism.py`) | `d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413` · s390x: **NOT_RUN** (BE-015) |
| branch `feat/p2-forecasting` @ `17e71af`+ — x86 `ruff format --check`, `ruff check`, `mypy`, `pytest` (`.venv`, no ML libraries) | 74 files formatted · ruff OK · mypy OK (74 files) · **152 passed, 17 skipped** (ML tests skip) |
| same, x86 with numpy/xgboost/snapml/tensorflow/torch/onnx (`PYTHONPATH=src pytest`) | **161 passed, 8 skipped** (P1 data tests) |
| LinuxONE `python -m pytest -q` in the ML owner's clone @ `1278294` (older commit of the branch) | **166 passed**; the latest commits are **NOT_RUN** on s390x |
| LinuxONE notebooks 01–03 on the real `data/v1` (XGBoost: 600-tree run; GRU run mode not recorded), metrics v2 | val MAE on clean windows baseline 0.105 / XGBoost 0.006 / GRU 0.077 m · leak contrast 4.16 / 3.39 / 1.80 · p50 per window 0.025 / 134.5 (with TreeSHAP) / 10.2 ms |
| branch `zanetti/be-011-forecaster-wiring` @ `4d8b8f0` — x86 `just check` (`.venv`, no ML libraries) | 75 files unchanged · ruff OK · mypy OK (75 files) · **159 passed, 18 skipped** (XGBoost wiring test skips without xgboost) |
| same, LinuxONE container `python -m pytest -q` (with ML libraries and `SENTINEL_DATA_DIR`) | **177 passed**; `tests/test_forecaster_wiring.py` 8 passed |
| LinuxONE `validate_replay_ingestion.py windows_2019.jsonl`, `SENTINEL_FORECASTER=xgboost` (`xgboost-33@0.1.0`), TreeSHAP off, `auto` / `compress_trees` | 105 120 windows each, 0 rejected, 0 processing issues, 0 mock alerts · forecast p50 1.371 / 0.651 ms · total p50 1.574 / 0.839 ms |
| same, TreeSHAP on, first 600 windows | 0 rejected, 0 issues · forecast p50 135.98 / p99 175.37 ms |
| x86 `just check` on PR #7 @ `94a1143` (`main` merged in, `uv sync --locked --group dashboard` as CI) | 114 files unchanged · ruff OK · mypy OK (114 files) · **615 passed, 19 skipped** |
| x86 `just check` on a local merge of PR #7 + PR #10 + PR #11 (not pushed) | 123 files unchanged · ruff OK · mypy OK (123 files) · **645 passed, 19 skipped** |
| LinuxONE container, same local merge: `python -m pytest -q` | **612 passed, 12 skipped** (streamlit tests skip: no s390x build); `test_determinism.py`, `test_forecaster_wiring.py`, `test_eval_run.py`, `test_dashboard_jupyter.py` 31 passed |
| LinuxONE, same merge: API on a private port (XGBoost `compress_trees`, TreeSHAP off, mock detector) + `sentinel.eval.run.record_run` with `windows_2019.jsonl` over HTTP | 105 120 windows, 0 rejected, 0 alerts, 0 missing, 320.9 s (≈ 328 windows/s, client and server on the VM) |
| same, XGBoost `auto` + TreeSHAP, mock detector σ 0.01 m (to get alerts), first 1000 windows; Jupyter dashboard (PR #11) live and on the recorded run | 1000 windows, 0 rejected, 9 alerts, all valid `Alert` with `forecast_contributions_m`; dashboard shows XGBoost `is_mock=false`, the SIMULATED banner and 9 alerts with contributions in both sources |
| shared API (port 8000) on `main` @ `333b1bd` (PRs #7, #10, #11 merged), `shared_api.sh restart` with `SENTINEL_FORECASTER=xgboost` (TreeSHAP off, `compress_trees`) + `scripts/replay.py windows_2019.jsonl` | `status`: forecaster `xgboost-33 0.1.0 is_mock=false`, detector mock · 105 120 × 201, 0 errors, 247.0 s, 425.6 windows/s, client p50 1.96 / p99 11.17 / max 204.5 ms · 0 alerts |
| line coverage | **NOT_RUN** — no coverage tool configured |
| branch `wip/be-012-docs-final`, base `56170f9` plus this documentation change — x86-64 (Intel Core Ultra 7 155H), Python 3.14.7: `just check` (ruff format, ruff check, mypy strict, pytest) | ruff format: 83 files left unchanged · ruff check: all checks passed · mypy: no issues found in 83 source files · **464 passed, 9 skipped** (8 P1 data tests and 1 offline-baseline real-data test, skipped without `SENTINEL_DATA_DIR`) |
| same, `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data uv run pytest tests/test_p1_data.py tests/detection/test_offline_baseline.py -q` | **48 passed** (the real-data tests ran; the offline one checks shape and finiteness only) |
| same, `uv run pytest tests/detection -q` (no `SENTINEL_DATA_DIR`) | **312 passed, 1 skipped** |
| same, `uv run pytest tests/test_detector_startup.py tests/test_config.py -q` | **39 passed** |
| README procedure with a synthetic calibration and regions (uvicorn on 127.0.0.1, `SENTINEL_DETECTOR=cusum`) | `/health` ok; `/v1/status` reports detector `cusum` `0.1.0+<8 hex>`, `is_mock: true`, `mocks_active: true`; `cusum` without the calibration variable, a missing calibration file and the calibration variable set with the mock detector each end in a `ConfigError` |
| manual end-to-end run on the real server, tree of `79f2647`, synthetic data (`docs/testing.md`) | 84 checks passed, no bug found; 330 windows over HTTP in 1.082 s, client p50 3.077 / p99 4.578 ms (`docs/performance.md`) |
| `SENTINEL_DETECTOR=cusum` on the s390x VM, the shared API | **NOT_RUN** — the shared API runs the mock detector (`scripts/shared_api.sh` does not forward `SENTINEL_DETECTOR*`) |
| BE-012g/i (this change), x86-64, Python 3.14.7: `uv run --group dashboard ruff format --check src tests scripts dashboard`, `ruff check …`, `mypy`, `pytest` | 121 files already formatted · ruff OK · mypy: no issues in 121 source files · **675 passed, 31 skipped** (22 tests need `$SENTINEL_DATA_DIR`: 8 P1 data tests, 1 offline-baseline real-data test and 13 on `network/v1/l-town.json`; 9 more need xgboost, snapml, tensorflow or torch and skip in both runs) |
| same, `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data uv run --group dashboard pytest -q` (the data directory with `network/v1/l-town.json`, sha256 `ff772dc1…`) | **697 passed, 9 skipped** (includes the checks on the real network and the server started from the environment with the official registry, the committed regions and `l-town.json`) |
| `uv run pytest tests/detection/test_network.py tests/detection/test_topology_build.py tests/detection/test_topology_files.py tests/detection/test_candidate_pipe.py tests/test_detector_network_startup.py tests/test_config.py -q` | all passed (see the full runs above for the counts) |
| `SENTINEL_DATA_DIR=$HOME/shared/civicwater/data uv run python -m sentinel.detection.offline.topology --check` | `config/regions.battledim.json: matches the rule on l-town.json` (exit 0); `l-town.json` has the same 905 pipes, nodes and lengths, the same pump and 3 PRVs and the same 5 zones as the rule computes from the pipes |
| scratch replay of the 2018 out-of-fold residuals of the BE-012f development run through `CusumDetector` with the L-Town regions and network (no 2019 file) | 105 120 windows in 13.3 s / 16.1 s; 1 487 alerts (same count as the development run), each with a candidate pipe; first alert of the 6 abrupt leaks 363, 2 892, 0, 346, 220, 323 m from the leaking pipe (ADR-011 Evidence; not a result) |
| determinism digest after BE-012i, x86-64 | `7c5ef97bf8ccee4e19695475142658b426be29f796298314ab054a277ad3eee3` (the replay without the new key gives the previous `d9106c15e4d5…`); s390x: **NOT_RUN** |
