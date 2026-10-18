# ADR-010 — Layer-2 evaluation protocol (2019, through the API)

- **Status:** Proposed — author: evaluation owner (P6). Needs review by the detection owner (P4)
  and must be **Accepted before the 2019 run** (ADR-008 §G).
- **Date:** 2026-10-17
- **Answers:** Q-UI-06 (ADR-008 §G rows "matching rule and tolerance per leak type" and
  "pipe → region table"; items (a)–(d)).
- **Implementation:** EV-002 (metrics and report) reuses
  `sentinel.detection.offline.layer1.evaluate` with the `MatchRule` fixed below; EV-003 (run
  through the API, recorder, manifest). Tickets in `.scratch/p6-dashboard-evaluation/issues/`.

## Context
ADR-008 splits the evaluation into layer 1 (detection owner, 2018 out-of-fold) and layer 2 (P6,
2019 through the deployed API, one run per final calibration, from an identified commit,
without restarting the server). Layer 1 already ships `evaluate()` with a PROVISIONAL
`MatchRule` whose window is `[start, min(end, start + max_delay[type])]`; layer 2 must fix the
rule before the run, or the 2019 result is not a test.

The official BattLeDIM scoring code (KIOS-Research/BattLeDIM on GitHub, `Scoring Algorithm/
Scoring_Algorithm.m` and `functions/scoring_function.m`, revision August 2020, read on
2026-10-17) defines a detection as successful when its time is inside `[leak start, leak end]`
**and** the reported pipe is within 300 m (pipe distance) of the leaking pipe; there is no
other delay limit. Repeated detections of an already detected leak score 0 (neither hit nor
false positive); a detection outside every leak's time-space window costs the repair-crew cost
(500 €); a hit scores the water saved from the detection to the leak's end at 0.80 €/m³ minus
`(distance / 300 m) × 500 €`. The official list has 23 leaks, including 4 that started in 2018
and are still active in 2019 (`p257`, `p427`, `p654`, `p810`); they count in TP/FN. The results
template page also mentions "Tmax = 1 week" as an example, but the scoring code does not use it.

Our alerts carry a region, not a pipe (contract §4), and the CUSUM has no closing event
(ADR-007).

## Problem
Fix, before seeing any 2019 alert, how alerts are matched to labelled leaks, which leaks count,
which metrics are reported and under which names, so the result is reproducible, comparable to
layer 1 and to the competition where it can be, and never overclaimed.

## Decision

1. **Primary matching rule = the official time window** `[start, end]` (`end` is the repair, or
   `2019-12-31 23:55` when unrepaired), both ends included. With `layer1.MatchRule` the window is
   `[start, min(end, start + max_delay)]`, so `max_delay` must exceed every label window: the
   inherited `p257` runs from 2018-01-08 to 2019-12-31 (722 days); use `max_delay` = 731 days
   for both types, checked by a test in EV-002 (a smaller value would cut the 4 inherited leaks). The detection time is the alert's
   `window_end` (simulated time), never `created_at`.
2. **Assignment, repeats, false alarms** — as `layer1.evaluate` (and the official code where it
   applies): alerts in time order; an alert detects the not-yet-detected leak with the earliest
   `start` among those whose window contains it (the official code picks the closest pipe; we
   have no pipe, see 7); an alert inside windows whose leaks are all detected is a **repeat**
   (reported, neither hit nor false alarm); an alert outside every window is a **false alarm**
   (ADR-008 §C). **Consequence:** with the official window and no pipe rule, `p257` is active over
   the whole of 2019, so every 2019 alert falls inside some window and the primary rule gives
   **0 false alarms by construction** (the competition discriminates with the 300 m pipe rule,
   which we cannot apply). False alarms are therefore reported **only from the 7-day view**
   (item 4), and every table says so.
3. **Leaks counted: all 23** of the 2019 labels, including the 4 inherited from 2018. Leaks that
   start before the first scored window (the first 144 windows of the run are unscored for lack
   of history) still count; the report lists which ones.
4. **Secondary views, same run, same alerts** (never replacing the primary):
   - *Operator view:* detected within 7 days of `start` (`max_delay` 7 days for both types);
     this view is the source of the false-alarm count and false alarms per week;
   - *New in 2019:* the 19 leaks that start in 2019 only.
5. **Metrics and names:** detected / total per leak type (abrupt, incipient) with the missed
   pipes listed; detection delay from `start` **and** from `peak` (median, max; per leak in the
   table); false alarms (count, times, per week over the replayed period); repeats; onset error
   = `suspected_onset − start` when the alert has one. Only counts, no confidence intervals
   (ADR-008 §F).
6. **Region accuracy: not computed** until (a) P4 delivers the L-Town sensor → region mapping
   (BE-012g) and (b) a pipe → region table for the 2019 labels exists (from P1's network data,
   committed before the run). Without both the report says "not computed".
7. **BattLeDIM score in euros: not computed.** It needs a candidate pipe per alert and pipe
   distances (Q-DET-07, DEP-DET-06). The name "BattLeDIM score" is used only if the official
   formula is applied unchanged.
8. **The run:** one replay of `windows_2019.jsonl` through a fresh API instance, recording every
   `WindowResult` and the client latency; alerts fetched by ID; a change of
   `/v1/status.started_at` invalidates the run. Manifest: commit and dirty flag, forecaster and
   detector `ModelInfo`, calibration artefact hash (`diagnostics.calibration_id`), data and label
   SHA-256 (`MANIFEST.json` of data v1), registry hash, aggregated `is_mock`, architecture,
   Python, `uv.lock` hash, this ADR's version.
9. **Wording and watermark:** every table carries "simulated benchmark (BattLeDIM L-Town);
   thresholds fixed on 2018; 2019 run once" (ADR-008 §H). A run with `is_mock=true`, a
   provisional registry or uncommitted code (ADR-008 §G: an identified commit) is watermarked
   **"NOT A RESULT"** and is not cited.
10. **Labels:** `eval/v1/leaks_2019.csv` is read only by `src/sentinel/eval/`; before the run, its
    23 rows are checked against the official list above (pipe, start, end, type). A mismatch
    stops the run.

## Alternatives considered
- **Tmax = 7 days as the primary rule** (first proposal): operationally meaningful, but not the
  competition's rule; kept as a secondary view.
- **Excluding the 4 inherited leaks:** the detector cannot see their onset, but the official
  scoring counts them; excluding them would inflate the detection rate. Kept in the primary,
  excluded only in the "new in 2019" view.
- **Writing a second matching implementation in the evaluation module:** duplicates layer 1 and
  risks the two layers disagreeing; reuse `layer1.evaluate`.

## Pros
Comparable with the competition's time rule and with layer 1; no constant invented; the operator
view is still visible.

## Cons
Without pipes, the official euro score and the distance rule cannot be applied, so our hits are
"time-only" hits: an alert in the wrong part of the network during a leak counts as a detection.
The report says so.

## Consequences
Q-UI-06 answered by this ADR (once Accepted). EV-002/EV-003 implement it. Changes after the run
produce results labelled "exploratory" (ADR-008 §G).

## Risks
- "Time-only" hits overstate localisation; mitigated by the region accuracy (when computable)
  and by stating the limitation in every table.
- Long leak windows (unrepaired leaks last until 31 December) make every alert a hit or a repeat
  under the primary rule; that is why false alarms come only from the 7-day view.

## Dependencies
ADR-007, ADR-008, BE-012g (regions), DEP-DET-05 (final calibration), Q-DET-07, data v1 labels.

## Revisit when
P4 delivers candidate pipes (then apply the official 300 m rule and euro score), or the
official rules document contradicts the scoring code read here.

## Review by the detection owner (P4), 2026-10-17

**Verdict: accept the protocol, with the changes below applied before this ADR becomes
Accepted.** Nothing in the primary rule (official `[start, end]` window, all 23 leaks, repeats
neither hit nor false alarm, `layer1.evaluate` reused, `max_delay` 731 days) conflicts with the
detector. The status stays `Proposed` until the evaluation owner applies the changes; the
decision is then recorded in the ADR-008 §G rows.

1. **Item 10 names the stale file.** `eval/v1/leaks_2019.csv` cuts the 4 inherited leaks at
   2019-01-01 (Q-DATA-07); the data owner's `eval/v2/leaks_2019.csv` (PR #14, 23 leaks, start and
   peak of 2018 for `p257`, `p427`, `p654`, `p810`, checked against the official list) replaces it.
   Use `eval/v2` and register its sha256 in the manifest.
2. **Items 6 and 7 are no longer blocked.** `config/regions.battledim.json` exists (BE-012g) and
   every alert may carry `candidate_pipe` (BE-012i, ADR-011, `Alert` 1.1; `null` without
   `SENTINEL_DETECTOR_NETWORK`). Region accuracy needs the pipe → region table: it is computable
   from `l-town.json` without a label (Q-DET-08 c), and the definition must be chosen and
   registered before the run. The official 300 m rule and the euro score can use
   `PipeNetwork.pipe_distance`; how pumps and valves count in the distance is open (Q-DET-08 d).
   Decide before the run whether the official pipe rule becomes the primary view (it removes the
   "0 false alarms by construction" effect of item 2) or stays a second view. The candidate pipe
   is a pipe to inspect, not a location, and has not been evaluated on 2018; say so in the table.
3. **Delays of the 4 inherited leaks are not delays.** Their `start` is in 2018, so
   "detection delay from `start`" would be hundreds of days and would dominate the median.
   Compute delays and onset error (item 5) over the 19 leaks that start in 2019 only, and list
   the 4 inherited ones as "active at the start of the run" with only detected / not detected.
4. **The 7-day false-alarm view charges the detector for the 4 inherited leaks.** They stay active
   all of 2019, so alerts that correctly report them fall outside every 7-day window and count
   as false alarms. Report the 7-day false alarms in two parts: those that fall in the window of
   an inherited leak (shown separately, not charged to the budget of Q-TEAM-03) and the rest.
   State the budget it is compared with and where it was fixed (ADR-008 §G).
5. **Manifest (item 8).** Add `diagnostics.regions_id` and the network id (`l-town.json` sha256)
   next to `calibration_id`, and the `config/regions.battledim.json` hash, so the run identifies
   everything the alert fields depend on.
6. **Calibration is still a development one.** Until DEP-DET-05 is delivered and the BE-012h
   artefact exists, any run is `is_mock = true` and watermarked "NOT A RESULT" (item 9); the
   rehearsal with `dev-baseline-2018` is a mechanics check only.
