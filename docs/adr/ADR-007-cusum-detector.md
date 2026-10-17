# ADR-007 — CUSUM detector: statistic, alert lifecycle, gaps, atomicity and regions

- **Status:** Proposed — author: detection owner. Needs confirmation by the ML owner (Q-ML-07:
  premise of §1), the dashboard owner (Q-UI-05: visibility of the detector state) and the
  evaluation owner (Q-UI-06). Settles the detector side of Q-DET-01..04 and the gap behaviour
  that ADR-004 left open ("Revisit when": detection owner).
- **Date:** 2026-10-17
- **Implementation:** `src/sentinel/detection/cusum.py`, `regions.py`, `calibration.py`
  (BE-012a..e). The selection by `SENTINEL_DETECTOR=cusum` is wired in `build_pipeline`
  (`src/sentinel/main.py`, `config.py`; `tests/test_detector_startup.py`, BE-012). Offline
  tools of BE-012f in `src/sentinel/detection/offline/`, development only and never imported by
  the server: `baseline.py` (out-of-fold residuals, monthly folds, slot of the week), `sweep.py`
  (replay of a `z` matrix on a regular grid, without gaps, with exact parity with this detector, and the parameter sweep) and
  `layer1.py` (delay and false-alarm counts; its matching rule is **provisional**, Q-UI-06).

## Context
The design document (§3) specifies a per-sensor CUSUM on the standardised residual,
`S_t = max(0, S_{t-1} + z_t − k)`, an alert when `S` crosses `h`, an onset estimate
("the last time `S` was zero") and a region ranking. Contract §3 fixes only the shape of the
output: a stateful, atomic, single-instance detector whose `alerts[]` holds alerts *opened at
this window*; it says nothing about when an alert opens, whether `S` is reset, or what
`suspect` means.

Facts measured on the data owner's files (data v1, outside git; `leaks_2018.csv`,
sha256 `5d97bef3…`):

- 14 labelled leaks in 2018, 6 abrupt and 8 incipient; 4 never repaired in 2018 (`p257`,
  `p427`, `p654`, `p810`).
- At least one leak is active in 97.9 % of the 2018 windows. No leak is active only from
  2018-01-01 to 2018-01-08 13:25 (7.6 days); two or more are active 93.9 % of the time,
  three or more 78.2 %, at most 6 at once.

## Problem
Define, without changing the contract schema, what opens an alert, how an alert's evidence is
kept and released, what the detector does when it sees missing windows, how it stays atomic,
and how it names a region.

## Decision

**1. Premise.** The CUSUM accumulates only if the residual stays displaced after the leak
starts, so the expected value must not follow the leak. The hour-of-week baseline and XGBoost
(design Table 2: inlet flow, tank level, time, other sensors) do not read the target sensor's
own recent pressure. The GRU (last 12–24 h) does and may track the leak. Every forecaster
declares which observed readings its expected value depends on (Q-ML-07). σ, k, h, N are
properties of the (forecaster, detector) pair and are bound to the forecaster's `ModelInfo`
(ADR-008). A forecaster is a source of alerts only if it passes the persistence test of
ADR-008.

**2. Statistic.** Per pressure sensor `i`:

```
z_i = (expected_i − observed_i) / σ_i        # positive when pressure is low (Q-DET-02)
S_i ← min(c·h, max(0, S_i + z_i − k))        # c > 1: cap
```

The payload carries the same `z` (and `deviation_m = observed − expected`, negative on a
drop); the CUSUM accumulates it as is.

σ_i, `k`, `h`, `N`, `c`, `h_close` come from the calibration artefact (ADR-008). No numeric
default exists in code.

**3. Opening.** An alert opens when at least `N` **unclaimed** sensors have `S_i > h`
(`N ≥ 1`). Opening does not depend on the sensor → region mapping.
`cusum.statistic` is the N-th largest `S_i` among unclaimed sensors and `cusum.threshold` is
`h`, so an alert opens iff `statistic > threshold`. `anomaly_score` carries the same value on
every window (dimensionless, σ units).

**4. Episodes per sensor, no reset.** The sensors that open an alert become *claimed*. A
claimed sensor does not count towards new alerts until `S_i < h_close` (`h_close ≤ h`).
`S` is never reset after an alert; the cap `c·h` bounds the recovery time after a repair. A
new alert opens when `N` other, unclaimed sensors cross `h`, so concurrent leaks are not
hidden by an open one. `alerts[]` carries only opened alerts; there is no closing event
(Q-DET-04 default).

**5. States.** `alarm`: at least one sensor is claimed. `suspect`: no claimed sensor, but an
unclaimed sensor has `S_i > h` (fewer than `N`). `normal`: otherwise. With `N = 1`,
`suspect` is empty.

**6. Onset.** `since_i` is the last window at which `S_i` was zero before the crossing;
`suspected_onset` is the earliest `since_i` among the alert's `deviating_sensors`. The rule is
literal: a new episode that starts before `S_i` returned to zero **inherits** the old
`since_i` (Q-DET-06). `suspected_onset` is `null` when any deviating sensor never had
`S_i = 0` since the first window the detector evaluated (its onset precedes the data the
detector saw).

**7. Gaps and restart.** The detector compares `timestamp` with the last window it evaluated.
With `m` missing windows (`Δ − 1`), every `S_i ← max(0, S_i − k·m)` before the current window
is processed, as if each missing window had zero residual. No interpolation, no gap
threshold. The backend calls `evaluate` only for scored windows, so the detector sees `m`
small after a failed window and `m ≥` the required history (144 by default) after a data gap
or a restart (ADR-003, ADR-004). `diagnostics` carries `skipped_windows`, and
`reset_reason="gap"` when a skip emptied every `S_i`. `timestamp ≤` the last evaluated one
raises `DetectionError`. The rule is deliberately conservative: it can only lower `S`.
Inside the gap, the last missing window counts as a zero of `S_i` (for `since_i`) whenever the
decay left `S_i` at 0, and a claimed sensor is released if `min(decayed S_i, new S_i) <
h_close`: the decayed value is the lowest `S_i` of the gap.

**8. Region.** Mapping file `config/regions.<name>.json`, shape
`{"regions": {"<region>": ["<sensor>", ...]}}`: content owned by the detection owner, built
from the network topology by a documented rule fixed before any evaluation. The order of the
regions in the file is the tie-break; each pressure sensor belongs to exactly one region and
the file must cover exactly the registry's pressure sensors (startup fails otherwise).
`region_scores` is emitted on every window with **every** region (`0.0` when empty): the sum
of `S_i` over the unclaimed sensors of the region with `S_i > h`, i.e. the same set as
`anomaly_score` (after the release by `h_close`, before the sensors of the alert that opens
in this window are claimed). `alert.region` is the region with the largest sum (tie: first
in the file); since the alert's `deviating_sensors` are exactly that set, the sums are the
alert's. Summing only evidence above `h` keeps sub-threshold noise out and keeps the ranking
independent of claimed (older) episodes. The aggregator (sum) is chosen a priori; mean and
max are reported only as a 2018 sensitivity analysis (ADR-008). Without a mapping `region`
and `region_scores` are null and no new key appears; with one, `diagnostics.regions_id` is the
sha256 of the file's bytes.

**9. Atomicity.** `evaluate` has two steps. Step 1 is pure: validate the input, compute the
new state as a new immutable object (no in-place mutation of arrays or dicts), build the
`DetectionResult`. Step 2 is a single reference assignment. Any exception before step 2
leaves the state untouched; this holds for any exception, because the pipeline catches
`Exception`. All-or-nothing: a sensor set different from the calibration's, a non-finite
value or `timestamp ≤` last raises `DetectionError`; `complete` is always `true`. σ has a
numeric floor so `z` is always finite. The detector has no lock of its own: the pipeline
serialises calls (ADR-005). Arithmetic is elementwise float64 with deterministic tie-breaking
(`(−S, registry index)`), so results match across x86 and s390x (parity hash, BE-015).

**10. Not in the first version.** Common-mode removal and clipping of `d`. Their adoption
criterion is in ADR-008.

## Alternatives considered
- **Reset `S` after each alert:** a persistent leak rebuilds `S` to `h` in about
  `h/(d − k)` windows and re-alerts all along, with the reset instant as a wrong onset.
- **One global episode (what the mock does):** hides new leaks in other regions while an
  episode is open; up to 6 leaks were active at once in 2018.
- **Alert on a sum over all sensors:** the design wording is ambiguous between sum and
  per-sensor `S`; the contract needs per-sensor evidence (`since`, `deviating_sensors`).
- **Gap threshold `G` (continue below, reset above):** no principled value; throws away
  evidence after every single failed window.
- **`complete=false` for partial sensor sets:** a partial `S_i` vector corrupts the gap rule.
- **Sum of all region sensors for the ranking:** biased by region size and by sub-threshold
  noise (each `S_i` has positive mean even without a leak), and pulled by claimed episodes.

## Pros
No schema change. One alert per episode and sensor group. Concurrent leaks handled. Onset
kept. A single decay rule covers failed windows, data gaps and restarts. No parameter exists
that the data cannot calibrate.

## Cons
More state than the mock (per-sensor `S`, claim flag, last-zero time). The cap, `h_close` and
`N` add three parameters to calibrate on few events (ADR-008).

## Consequences
- Contract §3 states the semantics above (text only). The mock `ThresholdMockDetector` stays
  as is and keeps `is_mock=true`.
- `SENTINEL_DETECTOR=cusum` is added to `build_pipeline` only together with the
  implementation (ADR-005); the startup checks of ADR-008 live in
  `CusumDetector.for_pipeline`. Status 2026-10-17: the detector is implemented (BE-012a..e)
  and selected by `SENTINEL_DETECTOR=cusum` with `SENTINEL_DETECTOR_CALIBRATION` (required) and
  `SENTINEL_DETECTOR_REGIONS` (optional); the server refuses to start with a `ConfigError` on any
  mismatch (README, `docs/integration-contracts.md` §3). The mechanics were also checked end to
  end against the real server with synthetic data (`docs/testing.md`).
- `state` and `anomaly_score` are visible only in the `POST /v1/windows` response
  (`detector_state`) and in the `window.*` audit payload; no GET endpoint exposes them
  (Q-UI-05).

## Risks
- An incipient leak that spreads to neighbouring sensors can open a second alert for the same
  leak; without the region mapping there is no merge. Measure before deciding.
- `suspect` can last for weeks when `N > 1` and one sensor stays above `h`.
- The dashboard cannot show "open alerts" or the detector state today (Q-UI-05).
- The contract's atomicity covers the detector only: if the pipeline discards a result the
  detector already returned (e.g. `invalid_detection`) or fails after it, the detector state
  has advanced while the window was lost.
- Sensors that share a disturbance (demand, pump, tank) have correlated residuals; this costs
  detection power and is handled empirically through the joint calibration of `N` and `h`
  (ADR-008), not by assumption.

## Dependencies
Q-ML-07 (premise), Q-UI-05 (state visibility), Q-UI-06 (evaluation), DEP-DET-04 (region
mapping), DEP-DET-05 (residuals for calibration), wiring in `build_pipeline` (ADR-005; done).

## Revisit when
The first real out-of-fold residuals are available (ADR-008), Q-UI-05 is answered, the region
mapping exists, or the persistence test rejects a forecaster that the team needs.
