# 10: EV-003 — In-process 2019 run with manifest

**What to build:** Gated by the 22:00 trigger: the real detector must be plugged into the VM API by 22:00 on 2026-10-17. If not, this ticket moves to `wontfix` for the deadline.

The P6 owner runs one command that builds the pipeline with the configured forecaster and detector, feeds the 2019 windows in order, collects every alert and produces three outputs:
- the EV-002 report;
- a reduced manifest: commit and dirty flag, model infos, detector configuration hash, data and label SHA-256, aggregated mock flag, architecture and Python;
- the run's alerts and status snapshot in the playback format.

The ground truth is read only from the evaluation data area.

**Blocked by:** 09, 02

**Status:** ready-for-agent

- [ ] A run over the 2019 windows completes; the counts of windows and alerts are reported
- [ ] The manifest has every listed field; a run with mocks gets the watermark
- [ ] The output alerts and status load in the dashboard's playback adapter
- [ ] The ground-truth path is not referenced by any non-evaluation code (guard test)
- [ ] `just check` green; run commands and results recorded in `docs/status.md`

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
