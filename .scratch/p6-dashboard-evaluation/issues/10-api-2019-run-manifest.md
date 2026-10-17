# 10: EV-003 — Layer-2 2019 run through the API, with manifest

**What to build:** Gated by the 22:00 trigger: the real detector must be plugged into the VM API
by 22:00 on 2026-10-17. If not, this ticket moves to `wontfix` for the deadline.

ADR-008 (detection owner) requires the layer-2 run to go **through the API**, without restarting
the server. This replaces the earlier in-process plan. The in-process path stays only as a fast
development mode.

The P6 owner runs one command (in the evaluation module, stdlib HTTP client) that:
- posts the 2019 windows (`windows_2019.jsonl`) in order to a fresh API instance;
- records every `WindowResult` and the client latency to `responses.jsonl`. This is P6's own
  recorder; `scripts/replay.py --responses-out` (Q-UI-04) is not needed;
- fetches every alert by ID to `alerts.jsonl` and snapshots `/v1/status`;
- aborts the run as invalid if `/v1/status.started_at` changes;
- writes the manifest: commit and dirty flag, model infos, calibration artefact hash
  (`diagnostics.calibration_id` / `ModelInfo.version`), data and label SHA-256, aggregated mock
  flag, architecture and Python;
- produces the EV-002 report.

The output alerts and status load in the dashboard's playback adapter. The ground truth is read
only from the evaluation data area.

**Blocked by:** 09, 02

**Status:** ready-for-agent

- [ ] A run over the 2019 windows completes through the API; counts of windows (105,120 or every
      rejection explained) and alerts reported
- [ ] Restart detection invalidates the run (tested with a stub)
- [ ] Manifest has every listed field; a run with mocks gets the watermark
- [ ] Output alerts and status load in the playback adapter
- [ ] The ground-truth path is not referenced by any non-evaluation code (guard test)
- [ ] `just check` green; run commands and results recorded in `docs/status.md`

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
