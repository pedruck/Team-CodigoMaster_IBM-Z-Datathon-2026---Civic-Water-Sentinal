# 08: EV-001 — ADR-010: layer-2 evaluation protocol (frozen before any 2019 run)

**What to build:** The detection owner's ADR-008 (calibration and evaluation, branch
merged into `main` in PR #5; layer-1 code `sentinel.detection.offline.layer1` with a PROVISIONAL `MatchRule`) assigns the **layer-2** evaluation to P6: one
2019 run through the API per final calibration, from an identified commit, without restarting
the server. Its pre-registration table (ADR-008 §G) leaves two rows to the evaluation owner
(Q-UI-06): the matching rule with its tolerance per leak type, and the pipe → region table for
the 2019 labels.

ADR-010 (P6) answers Q-UI-06 **before** the run and states:
- the alert-to-leak matching rule: Tmax per leak type (abrupt / incipient), earliest-alert
  matching, the duplicate policy, the false-alarm definition ("an alert opened outside the
  matching window of every labelled leak", ADR-008 §C);
- delay measured from the label `start` (official BattLeDIM measure) and from `peak`, as
  layer 1 does, so the two layers are comparable;
- how the 4 leaks active on 2019-01-01 and the first 144 unscored windows are counted;
- the pipe → region table for the 2019 labels, derived from P4's sensor → region file and
  P1's network data (provisional until both exist; region accuracy is "not computed" otherwise);
- metric names, the run manifest fields, the "NOT A RESULT" watermark and the wording of
  ADR-008 §H;
- the BattLeDIM score as "not computed" unless the official formula applies unchanged (Q-DET-07).

Any BattLeDIM constant is taken from the official rules and evaluation code, with the source
cited. P4 reviews it; reuse P4's layer-1 metric code (`sentinel.detection.offline.layer1`) once
merged, instead of a second implementation, if its definitions match.

**Blocked by:** None (can start immediately). The pipe → region table waits for P4's mapping
(BE-012g) and P1's graph.

**Status:** in progress (ADR-010 written, awaiting P4 review)

- [x] ADR-010 present, indexed, referencing ADR-008 §F–§H, every rule above stated
- [x] Q-UI-06 answered in `docs/questions-for-team.md` (pointing to ADR-010)
- [ ] P4 review recorded and status Accepted before EV-003 runs on 2019
- [x] Sources for any BattLeDIM constant cited

Spec: `.scratch/p6-dashboard-evaluation/spec.md`

## Comments

- 2026-10-17 — The decision changed after reading the official scoring code (KIOS-Research/
  BattLeDIM, `Scoring_Algorithm.m`, `scoring_function.m`): no Tmax (window `[start, end]`), the
  4 inherited leaks count (23 total), repeats score 0. Approved by P6 the same day: primary =
  official window with 23 leaks; "within 7 days" and "new in 2019" as secondary views.
- Pending: P4 review → status Accepted before EV-003.
