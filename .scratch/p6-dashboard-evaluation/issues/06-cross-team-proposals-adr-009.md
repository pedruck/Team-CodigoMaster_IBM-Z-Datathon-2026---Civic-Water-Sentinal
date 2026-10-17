# 06: UI-006 — Cross-team proposals and ADR-009 (dashboard)

**What to build:** The dashboard's gaps are filed as questions with owners and proposed shapes, so other owners can answer without P6 implementing their modules.

`docs/questions-for-team.md` gets:
- Q-UI-07 (P3+P4): residuals endpoint (Q-UI-05/06 are taken by P4's branch);
- Q-AUD-06 (P5+P3): audit read contract, superseding Q-AUD-04;
- Q-AUD-07 (P5+P3): explanation endpoint;
- Q-DET-04 raised (lifecycle: `state`, `closed_at`, `last_seen_window_end`);
- Q-DET-07 (P4): candidate pipe per alert (Q-DET-06 is taken by P4's branch);
- Q-UI-04 reworded (replay `--responses-out`, evaluation reads run artefacts);
- Q-UI-03 resolved (no CORS, server-side dashboard);
- the `not_applicable` doc drift (P3).

`docs/dependencies.md` is updated to match.

ADR-009 (dashboard architecture; ADR-007/008 are taken by P4's branch) is written as `Proposed`: server-side Python, runtime contract parsing, HTTP/playback seam, mandatory banners, simulated clock, polling. The proposed shapes are in the spec (section "Cross-team proposals").

**Blocked by:** None (can start immediately)

**Status:** done (2026-10-17)

- [x] Each new question has an owner, a priority, a proposed default and the dashboard's behaviour until answered
- [x] `docs/dependencies.md` rows added/updated (DEP-UI-03, DEP-AUD-02, DEP-AUD-04, DEP-DET-03)
- [x] ADR-009 present and indexed in the ADR README
- [x] CHANGELOG `Unreleased` updated

Spec: `.scratch/p6-dashboard-evaluation/spec.md`

## Comments

- 2026-10-17 — Done by a worktree subagent. IDs checked against every origin branch. The
  `not_applicable` drift exists only on unmerged branches (Q-DOC-01); runbook fix untested
  (Q-DOC-02). Review fixes: Q-DET-04 lifecycle fields proposed, DEP-DET-03 updated,
  DEP-DET-06 added for Q-DET-07.
