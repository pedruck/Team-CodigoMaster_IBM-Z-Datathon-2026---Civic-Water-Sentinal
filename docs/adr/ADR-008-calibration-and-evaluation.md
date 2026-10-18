# ADR-008 — Detector calibration and evaluation protocol

- **Status:** Proposed — author: detection owner. Needs confirmation by the ML owner
  (Q-ML-07, Q-ML-08), the evaluation owner (Q-UI-06) and the team (Q-TEAM-03).
- **Date:** 2026-10-17
- **Implementation:** §B and the startup checks, `calibrate()` (σ only) and the property and
  determinism tests: BE-012d (`src/sentinel/detection/calibration.py`, `cusum.py`). The startup
  checks run when `SENTINEL_DETECTOR=cusum` is selected: `build_pipeline` →
  `CusumDetector.for_pipeline` (`src/sentinel/main.py`; `tests/test_detector_startup.py`).
  Offline tools for §C, §E and §F (BE-012f, parts 1 and 2; development only, pandas, never
  imported by the server) in `src/sentinel/detection/offline/`: `baseline.py` (out-of-fold
  hour-of-week residuals, monthly folds, slot of the week; `write_oof` writes the residual and
  fold files and returns the two hashes for `calibrate()`), `sweep.py` (`replay` of a `z` matrix
  with exact parity with the detector, `sweep` over a grid of `h` and `N`) and `layer1.py`
  (counts and delays; the matching rule between an alert and a leak is **provisional**, Q-UI-06,
  and is an argument of every call, never a default). The development run itself is in progress.

## Context
The design document fixes the models, σ, k and h on 2018 only and replays the whole of 2019
through the deployed API, window by window (§3, §7). It also warns that 2018 "is not a clean
normal year" (§2). The data owner's files (data v1, outside git) show how unclean it is:
14 labelled leaks, at least one active in 97.9 % of the 2018 windows, no leak-free stretch
after 2018-01-08 13:25, four leaks never repaired (ADR-007, Context). The remedy suggested in
§2, "train on the stretches after the repairs", has no such stretch.

Residuals are not independent across sensors, are autocorrelated at 5 minutes and are
heteroscedastic, so the usual i.i.d. run-length formulas for the CUSUM do not apply: false
alarms must be measured by replay.

## Problem
Fix σ, k, h, N, c and h_close without real residuals of the final forecaster, without
inventing numbers, without overfitting 14 events, and without leaking the 2019 evaluation
set into the calibration.

## Decision

**A. Data.** The calibration uses 2018 only and only **out-of-fold** residuals of the
forecaster that will serve (time-block folds; never in-sample residuals, which are
optimistic and give a σ that is too small). The 2019 files are sealed for the detection
owner: the detector is evaluated on 2019 only by the evaluation owner, through the API
(design doc §7). Labels of 2018 are used for calibration and reporting; labels of 2019 are
never read by the detector owner.

**B. Calibration artefact.** A small JSON file `config/calibration.<forecaster>.json`
(about 40 numbers, no data), committed so that the pre-registration (G) is verifiable by
hash. It holds σ per sensor, `k`, `h`, `N`, `c`, `h_close`, the forecaster's `name` and
`version`, the sensor IDs, the hash of the input residual file and of the fold definition,
and `provenance ∈ {synthetic, dev-baseline-2018, battledim-2018-oof}`. Field names:
`provenance`, `forecaster {name, version}`, `residuals_sha256`, `folds_sha256`, `sigma_m`,
`k`, `h`, `n_min`, `c`, `h_close`.
- The file is written by `write_artefact` (canonical bytes: fields in declaration order,
  sensors in calibration order, two-space indent, final newline); the artefact hash is the
  sha256 of the file's bytes, so what is hashed in memory and on disk cannot differ.
- No numeric default for any of these exists in code.
- `provenance ≠ battledim-2018-oof` forces `ModelInfo.is_mock = true`, so
  `mocks_active` and `Alert.is_mock` flag every output of a development calibration.
- `ModelInfo.version` is `0.1.0+<first 8 hex of the artefact hash>` (limit: 32 characters);
  `diagnostics.calibration_id` carries the full hash.
- `CusumDetector.for_pipeline(...)` fails with `ConfigError` if the forecaster's
  `name`/`version` differ from the artefact's, or if the artefact's sensor IDs **or their
  order** differ from the registry's pressure sensors (ADR-005: fail fast; the order matters
  for the deterministic tie-break, ADR-007 §9).

**C. Estimation.**
- σ per sensor by median / MAD of the out-of-fold residual, with a numeric floor:
  `calibrate()` estimates only `σ = max(sigma_floor_m, 1.4826·MAD)`; `k`, `h`, `N`, `c`,
  `h_close`, the floor and the hashes are parameters without defaults. The 2018
  labels are used only as a sensitivity analysis (mask the windows with an active leak and
  report how σ changes). σ is documented as conservative; 2018 is never called "clean normal".
- `k` is fixed a priori from the smallest standardised shift δ worth detecting
  (`k = δ/2`); δ and its rationale are registered in the pre-registration record below
  *before* any calibration runs.
- `h` and `N` come from one joint sweep for the false-alarm budget (Q-TEAM-03).
  `c` and `h_close` are chosen for the recovery time after a repair, from the same sweep.
  Few parameters are tuned, and the sweep is validated leaving one labelled leak out at a time.
- "False alarm" cannot be "an alert with no leak active" (one is almost always active). It is
  an alert opened outside the matching window of every labelled leak (G).

**D. Persistence test (premise of ADR-007 §1).** For the 6 labelled abrupt leaks of 2018
(`p673`, `p538`, `p866`, `p183`, `p158`, `p369`), with the forecaster trained without the
month of the leak, measure how much of the initial residual displacement remains during the
24 h after the start. Real leaks are used instead of an injected step because they carry the
real spatial signature (neighbouring sensors). Incipient leaks have no step and are excluded.
The acceptance criterion is registered before the test runs. The GRU is a source of alerts
only if it passes; the two XGBoost variants of Q-ML-07 (with / without the other sensors'
current pressures) are compared on the same test and on the layer-1 curve; the detection
owner's prior is "without".

**E. Development stage.** Until the ML owner delivers residuals (DEP-DET-05), the detection
owner computes out-of-fold residuals of a local hour-of-week baseline in
`src/sentinel/detection/offline/` (BE-012f; the tools exist, the development run is in progress),
with `provenance = dev-baseline-2018`. Its purpose is to learn the
real residual structure, to test the σ estimator and to exercise the sweep. Its numbers never
become delivered results. Synthetic residuals are used only to test mechanics (state,
atomicity, determinism), through **property** tests (a larger step is detected earlier; a null
series gives a false-alarm rate close to the target; scale invariance), never "this `h` gives
X". The synthetic residual generator includes AR(1) noise, diurnal heteroscedasticity and a
common-mode component, so it is harder than the real signal, not easier.

*Finding of the sweep tool: the false-alarm count is not monotone in `h`.* One expects fewer
false alarms as `h` grows, and that holds in the test only for one sensor, `N = 1` and no cap
(`test_false_alarms_do_not_increase_with_h_for_one_sensor_without_a_cap`). In general it does
not, for three reasons: (1) the cap `c·h` is higher for a larger `h`, so `S` is higher after
a big excursion and, on a later rebound, can end above the larger `h` while the lower `S` of the
smaller `h` stays below it; (2) with `N > 1` the sensors of a group cross a larger `h` in
different windows, so the groups that open alerts, and the sensors left unclaimed, differ;
(3) the false alarms are counted against the matching windows of the labelled leaks (§F), so an
opening that moves in time can change class. Only (1) has a counterexample, verified on the real
`CusumDetector` and not only on the replay
(`test_a_larger_h_can_open_more_alerts_when_the_cap_is_active`: on the same series `h = 5.0`
opens one alert and `h = 5.5` opens two); (2) and (3) follow from the claim rule and the matching
rule and have no test of their own. Consequence: the table of a sweep is read cell by cell,
never interpolated between values of `h`, and `h` is not chosen from an assumed monotone curve.

**F. Evaluation layers.**
- *Layer 1 (detection owner, 2018 out-of-fold):* delay × false-alarms-per-week curve per
  forecaster and per leak type (abrupt, incipient). Delay reported from the label `start`
  (the official BattLeDIM measure) **and** from `peak`. Exact counts n/N, misses listed,
  region accuracy reported separately from detection. With 14 events only counts are
  reported, no confidence intervals.
- *Layer 2 (evaluation owner, 2019 through the API):* one run per final calibration, from an
  identified commit, without restarting the server (the detector state does not survive a
  restart, ADR-003).
- The ladder of the design document (each model must beat the previous one) is evaluated
  with the same detector recalibrated for each forecaster (B).

**G. Pre-registration.** Before the layer-2 run, one commit records the fields of the table
below. None of them changes after the 2019 result is seen. A change made after seeing 2019
produces results labelled "exploratory" in every table.

| Field | Value | Owner |
|---|---|---|
| δ (→ `k = δ/2`) and rationale | not yet registered | detection |
| Persistence-test acceptance criterion | not yet registered | detection |
| False-alarm budget (alerts per week) | not yet registered (Q-TEAM-03) | team |
| Matching rule and tolerance per leak type | not yet registered (Q-UI-06) | evaluation |
| Sensor → region mapping and pipe network (rule, D, files) | registered by ADR-011 §1–2: `config/regions.battledim.json` sha256 `ca365b7074d2bb3df2cec38d69768850ca1d37b9a717db62a9f95a71a25fe57d`, built from the data owner's `network/v1/l-town.json` sha256 `ff772dc1b68c59c05dc3a383fe739a624c054a7fd7c7544bc3b8c6bcbbf49b59` (model `L-TOWN.inp` sha256 `a7551b86745f4cc3433c78e60023077fa1386947d4d35372ffd3a0405622b436`) | detection |
| Pipe → region table (2019 labels) | not yet registered (Q-UI-06) | evaluation |
| Calibration artefact hash | not yet registered | detection |
| Commit of the run | not yet registered | evaluation |

**H. Wording.** Every results table carries "simulated benchmark (BattLeDIM L-Town);
thresholds fixed on 2018; 2019 run once". Results are never described as validated on a real
network (AGENTS §7; design doc §8). `is_mock=false` means "real detector", not "real network".

**I. Backlog with adoption criterion.** Common-mode removal (subtracting the cross-sensor
median) and clipping of `d` are adopted only if, on 2018 out-of-fold with one leak left out
at a time, they lower the false alarms per week at equal or better delay for the residuals of
**both** the baseline and the XGBoost, and do not lose any labelled abrupt leak that was
detected without them. Until then the detection owner only measures the cross-sensor
correlation and the rate of extreme single-window residuals.

## Alternatives considered
- **Calibrate on synthetic data and "adjust later":** any `h` looks good on i.i.d. noise;
  numbers of an invented signal would reach the alerts.
- **Calibrate on in-sample 2018 residuals:** σ too small, false alarms in production.
- **Calibrate or tune the aggregator on 2019:** destroys the test set.
- **One set of thresholds for all forecasters:** the residual scale and persistence differ per
  model (ADR-007 §1).
- **Defaults in code taken from the contract example (`k=0.5`, `h=5.0`):** the example values
  are illustrative (design doc §6), not results.

## Pros
Every number has a provenance. A development calibration cannot be mistaken for a final one
(`is_mock`). The 2019 result stays a test.

## Cons
The final numbers wait for the ML owner's residuals. 14 events limit what any sweep can show.
The calibration artefact in git is a (small) derived file.

## Consequences
New dependency DEP-DET-05. Questions Q-ML-07, Q-ML-08, Q-TEAM-03, Q-UI-05, Q-UI-06. The
detector refuses to start with an artefact that does not match the forecaster or the
registry (`CusumDetector.for_pipeline`); a synthetic artefact (placeholder IDs, tests) or a
development one declares `is_mock=true`. The real registry exists (BE-010), so a real
artefact only needs DEP-DET-05.

## Risks
- Only 14 labelled events (6 abrupt), so any tuning can overfit; few parameters are tuned.
- The false-alarm count is not monotone in `h` (§E): a sweep table cannot be interpolated, and
  the operating point is read from the replayed cells.
- The matching rule used by the layer-1 tool is provisional (Q-UI-06); every delay and
  false-alarm count computed with it is provisional too.
- σ is inflated by leaks inside 2018, which lowers sensitivity (conservative direction).
- Leaks still active from 2018 at the start of 2019, and the first 144 windows that are not
  scored for lack of history, may be counted as hits or false alarms depending on the
  evaluation rule (Q-UI-06).
- A development calibration may be presented as final; `is_mock` and the pre-registration
  table are the guards.

## Dependencies
DEP-DET-05 (out-of-fold residuals), Q-ML-07, Q-ML-08, Q-TEAM-03, Q-UI-06, data v1 files and
their `MANIFEST.json` hashes (data owner).

## Revisit when
The ML owner delivers real out-of-fold residuals, the layer-1 curve shows that the budget is
unreachable, or the persistence test rejects the forecaster that is meant to serve.
