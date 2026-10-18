# P4 — Calibration of the CUSUM detector on the XGBoost out-of-fold residuals

> Simulated benchmark (BattLeDIM L-Town); every number below is fixed on **2018** out-of-fold
> residuals; no 2019 file was opened; the matching rule is the operator view of ADR-010 (7 days),
> provisional until Q-UI-06 is registered; counts n/N only, no confidence intervals (ADR-008 §F).
> Nothing here is validated on a real network.

BE-012j (the tools) and BE-012h (the run) of `docs/implementation-plan.md`. Decisions: ADR-007
(detector), ADR-008 §C, §D, §G (calibration and pre-registration). Inputs: the ML owner's
out-of-fold residuals of `xgboost-33@0.1.0` (DEP-DET-05, PR #19), both Q-ML-07 variants.

## 1. How each parameter is defined

| Parameter | How | Check |
|---|---|---|
| `σ_i` | **Estimated**, not tuned: `1.4826·MAD` of the out-of-fold residual of every 2018 window (ADR-008 §C), `calibration.robust_sigma` | σ with the leak windows masked (`sentinel.data.clean_mask`: `reparados`, `reparados+crescimento`, `todos`), reported only |
| floor of σ | The rounding noise of the measurement: `Δ/√12` of the coarsest sensor, `Δ` = smallest non-zero step between distinct observed values | False alarms per week **per sensor** with `N = 1` for the floors `Δ/√12`, `Δ`, 5, 10, 20 mm |
| `k` | **A priori**: `k_N = δ_N/2`, `δ_N` = the smallest, over the 6 abrupt leaks of 2018, of the `N`-th largest sustained displacement of `z` (mean over the 24 h after `start` minus the 24 h before). Fixed after phase A, before any replay | `k` × {½, 2} (that is `δ/4` and `δ`), reported only, never used to choose |
| `h`, `N` | **Joint sweep** (`replay`), `h` on a log grid (2·√2ⁱ, 2 to 1020) whose bounds follow the measured variance inflation `(1+φ)/(1−φ)`, `N` ∈ 1..5 (each with its `k_N`) | Pareto front, selection rule, leave-one-leak-out |
| `c`, `h_close` | **Recovery** after the 10 repairs of 2018: `c` ∈ {1.5, 2, 3} × `h_close/h` ∈ {0.25, 0.5, 0.75} at the chosen `(k, h, N)` | The chosen cell is re-evaluated (the cap changes the false alarms, ADR-008 §E) |

**Selection rule.** Among the cells with at most `B` false alarms per week: the most leaks
detected, then the smallest median delay from `start`; ties go to the larger `h`, then the larger
`N`. `B` is the team's (Q-TEAM-03); the run reports the operating point for the candidate budgets
0.5, 1, 2 and 4 per week, with **1 per week** as the primary (proposal of 2026-10-17).

**Leave-one-leak-out (LOLO).** For each of the 14 leaks, the openings inside its matching window
are dropped, the period shrinks by that window, and the rule picks a cell on the other 13; the
held-out leak is then looked up in the chosen cell. The report gives n/14 held out against n/14
in sample (the difference is the optimism of the selection) and the number of distinct cells.
If the choice is unstable, the most conservative of the chosen cells (largest `h`, then `N`) is
kept.

**Cap and release.** Within the budget: most leaks detected, then most repaired leaks whose alert
sensors all fall below `h_close` within **24 h** of the repair, smallest median recovery, fewest
repeat openings, then smaller `c` and larger `h_close`.

**Matching rule.** The operator view of ADR-010: `max_delay` = 7 days for both leak types, so the
2018 false alarms compare with those of 2019, which come only from that view.

**Why a grid with leave-one-out and not a regression or a surrogate.** One replay of 2018 takes
about 0.5 s, so the full grid is cheap and exact. The objective is a count over 14 leaks, a step
function, and the false alarms are not monotone in `h` (ADR-008 §E): a smooth model would
interpolate what cannot be interpolated. The real risk is overfitting 14 events (6 abrupt); the
remedy is to tune few parameters and validate by leaving one leak out. Fitting is used only to
**estimate** (σ from the MAD, δ from the abrupt leaks, φ to bound the grid).

## 2. Pre-registration trail (ADR-008 §G)

| Step | Commit | Content |
|---|---|---|
| Exploration rules | `ae93951` | Code of the tools and `exploration` of `config/calibration-protocol.xgboost-33.json` (σ mask, floor rule, persistence spans and thresholds, δ rule) and the pinned hashes of the residual files, **before any residual value was read** |
| Phase A (`explore`) | — | `explore.json` per variant: `293c2a99…` (with neighbours), `b958461a…` (without); no replay |
| Sweep rules | `6c0be6c` | `sweep` of the protocol: `k_by_n` copied from phase A, the grids, the matching view, the budgets, the recovery target; ADR-008 §C and §G rows; **before any replay** |
| Phase B (`sweep`) | `ca2ca8f` | Results below; `sweep` recomputes phase A and refuses a `k_by_n` that is not the explored one |

The protocol is validated by pydantic without defaults (`offline/experiment.py`). The δ rule
amends the "δ = 1σ" proposal of 2026-10-17 (PR #16), before any detection result and before 2019.

## 3. Reproduce

```bash
export SENTINEL_DATA_DIR=$HOME/shared/civicwater/data
V=with-neighbours   # or without-neighbours
python -m sentinel.detection.offline.experiment explore \
    --protocol config/calibration-protocol.xgboost-33.json --variant $V \
    --residuals-dir $SENTINEL_DATA_DIR/v1/oof/xgboost-33@0.1.0/$V \
    --out-dir $SENTINEL_DATA_DIR/p4_detect/xgboost-33-oof/$V
python -m sentinel.detection.offline.experiment sweep ... --provenance battledim-2018-oof
```

Outputs (outside git, `$SENTINEL_DATA_DIR/p4_detect/xgboost-33-oof/<variant>/`): `explore.json`,
`explore.md`, `sweep.csv`, `pareto.csv`, `lolo.csv`, `recovery.csv`, `k_sensitivity.csv`,
`floors.csv`, `report.md` and `calibration.<forecaster>.candidate.json`. Two runs of `sweep`
(commits `6c0be6c` and `ca2ca8f`, whose experiment code differs only in how the report checks for
uncommitted changes) gave the same 16 CSV, JSON and artefact files byte for byte (x86-64,
2026-10-18). Time: about 4 min per variant, almost all of it in the replays (`docs/performance.md`).

## 4. Phase A results

| | with neighbours (`xgboost-33@0.1.0`) | without neighbours (candidate) |
|---|---|---|
| σ (mm), min / median / max | 3.78 / 6.07 / 39.88 | 5.15 / 21.59 / 85.12 |
| σ with `reparados+crescimento` / σ with all windows (median) | 0.82 | 0.61 |
| `Δ` (all 33 sensors) → floor `Δ/√12` | 10 mm → 2.887 mm (never binds) | same (never binds) |
| lag-1 autocorrelation φ, min / median / max | 0.33 / 0.82 / 0.98 | 0.69 / 0.88 / 0.96 |
| persistence median / minimum (needs ≥ 0.5 / ≥ 0.25) | 0.957 / 0.539 (p538): **PASS** | 0.962 / 0.434 (p183): **PASS** |
| `k_N` for N = 1..5 | 6.34, 1.72, 1.08, 0.45, 0.27 | 4.21, 1.58, 1.49, 0.96, 0.60 |

Notes. The leak masks lower σ by 18 % (with) and 39 % (without): σ over all windows is the
conservative choice of ADR-008 §C. In `p866` the repair of `p538` (2018-06-02 06:05) falls inside
the 24 h measured, which lowers its remaining displacement on the sensors the two leaks share.

## 5. Phase B results

Operating point per candidate budget (false alarms per week, operator view). "Detected" is in
sample at the final operating point; LOLO is the held-out count of the `(h, N)` choice.

**With neighbours (`xgboost-33@0.1.0`, the served model):**

| B /week | operating point (k, h, N, c, h_close) | detected (abrupt, incipient) | FA /week | LOLO | stable | recovered ≤ 24 h |
|---|---|---|---|---|---|---|
| 0.5 | 0.27, 128, 5, 2, 64 | 7/14 (6/6, 1/8) | 0.48 | 7/14 | yes | 1/7 |
| **1** | **0.45, 128, 4, 1.5, 96** | **8/14 (6/6, 2/8)** | **0.84** | 8/14 | no (2 cells) | 6/7 |
| 2 | 0.45, 45.3, 4, 2, 34.0 | 9/14 (6/6, 3/8) | 1.78 | 9/14 | no | 5/7 |
| 4 | 0.27, 16, 5, 1.5, 4 | 14/14 (6/6, 8/8) | 3.63 | 14/14 | yes | 6/10 |

**Without neighbours (candidate, not an exported model):**

| B /week | operating point (k, h, N, c, h_close) | detected (abrupt, incipient) | FA /week | LOLO | stable | recovered ≤ 24 h |
|---|---|---|---|---|---|---|
| 0.5 | 1.49, 256, 3, 2, 192 | 10/14 (6/6, 4/8) | 0.44 | 9/14 | no | 7/8 |
| **1** | **0.60, 45.3, 5, 1.5, 11.3** | **12/14 (6/6, 6/8)** | **0.997** | 12/14 | yes | 6/9 |
| 2 | 0.60, 22.6, 5, 2, 11.3 | 13/14 (6/6, 7/8) | 1.84 | 13/14 | yes | 7/9 |
| 4 | 1.49, 22.6, 3, 1.5, 17.0 | 13/14 (6/6, 7/8) | 3.99 | 12/14 | no | 9/9 |

Delay from `start` of the 6 abrupt leaks at the primary point (hours, in order p673, p538, p866,
p183, p158, p369): with neighbours 4.75, 4.0, 3.92, 5.58, 2.75, 11.67 (median **4.4 h**); without
neighbours 4.25, 0.83, 0.67, 1.67, 3.83, 1.42 (median **1.5 h**).

**Placebo (post-hoc diagnostic, not part of the registered protocol).** All labels moved by one
common random offset (circular in 2018, 14–351 days, 1000 draws) and matched again with the same
openings of the primary point:

| | abrupt: real / placebo median (share of draws ≥ real) | incipient: real / placebo median (share ≥ real) |
|---|---|---|
| with neighbours | 6/6 / 3 (0.000) | 2/8 / 3 (0.92) |
| without neighbours | 6/6 / 3 (0.010) | 6/8 / 4 (0.14) |

With about one false alarm per week, a random alert falls inside a 7-day window with probability
about 1 − e⁻¹ ≈ 63 %. The **abrupt** detections, with delays of hours, are well above chance; the
**incipient** counts are not distinguishable from chance and must not be presented as detections
of incipient leaks. The registered selection rule counts them anyway (it was fixed before this
was measured); a rule that weighs abrupt detections or a shorter window for incipient leaks would
be a new registration, for Q-UI-06.

**Q-ML-07 (with or without the neighbours' pressures).** At the primary budget the variant
**without** neighbours detects the abrupt leaks about 3 h earlier (median 1.5 h vs 4.4 h) at a
similar false-alarm rate (0.997 vs 0.84 per week), passes persistence with the same median, and
its choice is stable under LOLO. This is the detection owner's prior (ADR-008 §D) and agrees with
the ML owner's leak contrast (5.62 vs 4.71). It is not a servable model yet: the ML owner would
have to export it (PR #19, "if without wins").

**k sensitivity (reported only).** With neighbours, `k/2` would detect 10/14 at 0.79 per week
(budget 1; the extra ones are incipient, see the placebo) and `2k` 8/14; without neighbours `k/2`
12/14 at 0.81 and `2k` 11/14. The registered `k` stands.

**False alarms per sensor (N = 1).** The floor matters little (all σ are above 3.78 mm). The
sensors that open the most single-sensor alerts are those with a large σ and a high φ: `n1`
(1.3 and 1.6 per week), `n4`, `n31`; not `n215` or `n740`, which were suspected before the data.
This supports `N ≥ 3`, which the selection chose at every budget for both variants.

**Candidate artefacts (outside git, not final).** Primary budget, `provenance =
battledim-2018-oof`, `is_mock = false`:

- `calibration.xgboost-33.candidate.json` (k 0.45, h 128, N 4, c 1.5, h_close 96), sha256
  `fad6ec0bded539cd326b6e53f77d7504bdeef68d4800427fcee26acd3b874256`;
- `calibration.xgboost-33-without-neighbours.candidate.json` (k 0.6, h 45.3, N 5, c 1.5,
  h_close 11.325), sha256 `d289eba6546a986405ed0a529f2247c8afc84cd42f43b268e64e72d237bb4f73`.

The final artefact `config/calibration.xgboost-33.json` is written and committed only after the
team fixes `B` (Q-TEAM-03) and, if the variant without neighbours is chosen, after the ML owner
exports it (its artefact must name the exported `name`/`version`, or `for_pipeline` refuses it).

## 6. Limitations

- 14 events (6 abrupt): counts n/N only; any selection on them can overfit (LOLO measures it).
- The incipient detections in the 7-day view are at chance level (placebo above).
- The matching rule is provisional (Q-UI-06); every delay and false-alarm count depends on it.
- Data are simulated (L-Town); `is_mock = false` means a real detector, not a real network.
- The variant without neighbours has no exported model; its candidate artefact cannot be served.
- The run is on x86-64; the s390x run of these tools was not done (only the server needs s390x).

## 7. v1 decision and the upgrade path (2026-10-18)

**v1 serves the variant with neighbours.** The model on the shared API is `xgboost-33@0.1.0`
(flows, tank level, calendar and the other 32 pressures at the same instant), so its calibration is
the one that can be served today. `config/calibration.xgboost-33.json` is the candidate artefact of
§5 at the primary budget (1 false alarm per week), byte for byte, sha256
`fad6ec0bded539cd326b6e53f77d7504bdeef68d4800427fcee26acd3b874256`; `provenance = battledim-2018-oof`, so `models.detector.is_mock = false`.
Start it as in `docs/runbook-vm.md` ("Starting the CUSUM detector").

What v1 gives and does not give (2018 out-of-fold, operator view, 14 events; not a result on a real
network): 8/14 leaks detected at 0.84 false alarms per week, abrupt 6/6 with a median delay of
4.4 h. Incipient counts are at chance level. It misses 6 of the 14 leaks, mostly incipient.

**The variant without neighbours is better on the same data, and is the planned upgrade.** At the
same budget it detects 12/14 (abrupt 6/6) with a median delay of 1.5 h, 0.997 false alarms per
week, and its choice is stable under leave-one-leak-out (§5). Its expected value does not follow a
leak through the neighbouring sensors (ADR-007 §1). It is not served because it is not an exported
model. To switch, in this order:

1. P2 exports the variant as a model with its own `name` and `version` (Q-ML-07) and registers it
   in the forecaster selection.
2. Regenerate its artefact with those names (`sweep` of §3 with `--variant without-neighbours`; the
   candidate of §5 names `xgboost-33-without-neighbours@candidate`, which `for_pipeline` refuses
   against any other served model). The protocol and the budget stay those registered; nothing is
   re-tuned.
3. Commit `config/calibration.<name>.json`, add its hash to ADR-008 §G and start the server with
   `SENTINEL_DETECTOR_CALIBRATION` pointing at it. The artefact of v1 stays, because it is the one
   that matches the model the shared API serves until then.

If the 2019 run (EV-003) is made with v1, the switch means a second run, reported as such; the
variant is not chosen after seeing 2019.
