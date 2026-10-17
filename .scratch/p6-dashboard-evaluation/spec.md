# Spec: P6 operator dashboard (MVP + playback) and offline 2019 evaluation

Status: ready-for-agent
Owner: P6 (dashboard, final integration, evaluation)
Source: grilling session 2026-10-17 (Q1–Q14)

## Problem Statement

CivicWater Sentinel ingests 5-minute SCADA windows of the simulated BattLeDIM L-Town network, forecasts expected pressures, runs a leak detector and serves alerts through a read API. Nobody can see that today. There is no screen where an operator or a jury member can watch alerts arrive. There is also no honest, reproducible answer to "how well does it detect the 2019 leaks?".

The P6 owner (dashboard, final integration, evaluation) has less than one day: the submission deadline is 2026-10-18 10:00. Several things are still missing:
- The forecaster and detector are still mocks.
- The audit sink, the narrative generator and the alert lifecycle do not exist yet.
- Read endpoints for residuals, audit history and explanations do not exist.
- The VM, the GRU model or the LLM may fail on demo day.

The risks are concrete:
- The dashboard works only with hand-made fixtures and breaks against the real API.
- Ground-truth leak labels leak into the operator view or the demo narrative.
- Mock or replayed output is presented as live detection.
- Evaluation numbers are produced in a way that cannot be reproduced or that quietly tuned the detector on the test year.

## Solution

**1. A read-only operator dashboard**, written in Python and rendered server-side. It reads only the existing read API: alert list, alert detail, status and health.
- Every response is parsed with the project's own contract models. If the API drifts from the contract, the screen shows a "contract mismatch" state instead of wrong data.
- The dashboard has one data-source seam with two adapters:
  - **live HTTP**, pointed at a backend instance;
  - **playback**, which reads a recorded run from files in the same contract.

  Playback is the demo contingency and the test fixture source at the same time. It is always marked with a visible "RECORDED REPLAY" banner.
- Mock output is always marked "SIMULATED".
- The clock shown is the simulated time reported by the backend, never the browser clock.

**2. An offline evaluation module.** It compares the alerts of one 2019 run with the 2019 ground truth.
- It uses a matching rule frozen in an ADR **before** the first run.
- It produces a report with a run manifest that makes the run reproducible.
- Every report carries a fixed "simulated data, not validated on a real network" header.
- A run that used mocks or provisional sensor IDs is watermarked "NOT A RESULT".

Ground-truth labels are read only by this module. The operator dashboard never sees them.

**3. Cross-team proposals for what the API does not offer yet.** These are residuals over time, an audit read contract, LLM explanations, an alert lifecycle and a candidate pipe for the official BattLeDIM score. They are filed as questions with owners instead of being worked around in the dashboard.

## User Stories

### Operator view

1. As an operator, I want a "Recent alerts" list ordered by the window in which each alert opened, so that I see the newest suspected leaks first.
2. As an operator, I want each alert row to show its window end, region, number of deviating sensors, the strongest deviation and its z, and the estimated onset, so that I can triage without opening the alert.
3. As an operator, I want an alert with no region to show "region not available", so that a missing sensor-to-region mapping never breaks the screen or looks like an error.
4. As an operator, I want an alert with no estimated onset, no CUSUM evidence, no forecast contributions or no summary to render with an explicit "not provided" marker, so that I know the field is absent rather than zero.
5. As an operator, I want an alert with an empty deviating-sensor list to still render, so that a contract-valid but unusual alert never crashes the page.
6. As an operator, I want an alert detail view with the structured evidence:
   - each deviating sensor with its deviation in metres, its z and the time since it started deviating;
   - the CUSUM statistic against its threshold;
   - the estimated onset and hours since onset, as computed by the backend;
   - the forecaster contributions for the deviating sensors, when present;
   - the detector summary;
   - the forecaster and detector name and version.

   This way I can understand why the alert fired.
7. As an operator, I want the hours since onset shown exactly as the backend computed them, so that the dashboard never recomputes evidence from the browser clock.
8. As an operator, I want the server's stored-at time labelled "recorded by server" and shown only in the detail view, so that I do not confuse wall-clock time with simulated time.
9. As an operator, I want the header to show the current simulated time (the last accepted window), so that "now" means the replay's now, even when the replay runs faster than real time.
10. As an operator, I want each alert's age computed as simulated now minus window end, so that ages stay meaningful during accelerated replay.
11. As an operator, I want the header to show which forecaster and detector are running, with their versions, so that I know what produced the alerts.
12. As an operator, I want a prominent "SIMULATED" badge whenever mocks are active or an alert says it came from a mock, so that mock output is never mistaken for leak detection.
13. As an operator, I want the audit reference of an alert (sequence and hash) shown as-is, or "audit unavailable" when it is null, so that I know whether the alert reached the audit log.
14. As an operator, I want the dashboard to show "explanation unavailable" where a narrative would go, so that the missing LLM explanation is explicit and the structured evidence remains the source of truth.
15. As an operator, I want the list to refresh automatically about every 5 seconds, so that new alerts appear without reloading.
16. As an operator, I want an alert that already appeared to never appear twice after a refresh, so that the list stays trustworthy.
17. As an operator, I want a paginated alert history, so that I can browse older alerts beyond the first page.
18. As an operator, I want to filter the history by time range and region, so that I can focus on one period or area.
19. As an operator, I want a loading state on first load, so that an empty screen is not mistaken for "no alerts".
20. As an operator, I want an empty state that says "no alerts in this period", so that zero alerts is clearly a result and not a failure.
21. As an operator, I want an "API unavailable since hh:mm" banner when the backend does not answer, with automatic retry and back-off, so that I know the data is stale and the dashboard is not frozen.
22. As an operator, I want a "contract mismatch" state when a response does not match the expected schema, so that the dashboard never renders partially wrong data.
23. As an operator, I want a status panel with the history fill, counters, per-stage latency (p50/p95/p99), throughput, error rate and the in-memory persistence warning, so that I can judge system health.
24. As an operator, I want to see whether the sensor registry is provisional, so that I know if real sensor IDs are in use.

### Demo and contingency

25. As the demo presenter, I want to switch the dashboard between a live backend and a recorded run by configuration only, so that I can recover from a VM or model failure without code changes.
26. As the demo presenter, I want a permanent "RECORDED REPLAY — run <id>, <date>" banner in playback mode, so that a recording is never presented as live.
27. As the demo presenter, I want to run the dashboard on an x86 laptop against the VM's API through an SSH tunnel, so that the demo does not depend on the dashboard's dependencies installing on s390x.
28. As the demo presenter, I want the dashboard to show which forecaster is actually running, so that a fallback model is visible and never disguised.
29. As a jury member, I want simulated data, mocks and recordings to be labelled on screen, so that I can trust what is claimed.
30. As the demo presenter, I want a recorded run with a small, known set of alerts, including null regions, a missing audit reference and the mock flag, so that I can rehearse every UI state before the demo.

### Contract safety

31. As the P6 owner, I want every fixture and every playback file validated against the contract models in the normal test suite, so that a schema change by another owner breaks their pull request, not my demo.
32. As the P3 owner, I want the dashboard to depend only on the contract models and the read API, never on services or model code, so that I can change internals freely.
33. As the P6 owner, I want fixtures that cover every nullable field and the edge cases the contract allows, so that "pretty" fixtures do not hide incompatibilities.
34. As the P6 owner, I want a CI guard that fails if the dashboard code references leak labels or the ground-truth files, so that labels cannot leak into the operator view.

### Evaluation

35. As the P6 owner, I want the alert-to-leak matching rule written and accepted in an ADR before the first 2019 run with a real detector, so that the rule cannot be chosen after seeing the results.
36. As the P4 owner, I want to review the matching rule, so that it is fair to the detector's semantics without me writing my own grade.
37. As the P6 owner, I want the evaluation to read the 2019 ground truth only from the evaluation data area, so that the detector, training and calibration never touch it.
38. As the P6 owner, I want detected leaks, missed leaks, detection delay (alert window end minus true start), onset error (estimated onset minus true start) and false alarms per week, so that the report states detection quality with clearly named metrics.
39. As the P6 owner, I want the report to say how the 4 leaks already active on 2019-01-01 were treated, so that their inclusion or exclusion is explicit.
40. As the P6 owner, I want region accuracy reported as "not computed" while the detector emits no region, so that no number is invented.
41. As the P6 owner, I want the official BattLeDIM score reported as "not computed — the system localises by region, not by pipe" unless the official formula can be applied unchanged, so that no adapted number carries the BattLeDIM name.
42. As the P6 owner, I want each run to write a manifest, so that anyone can reproduce or audit the run. The manifest holds:
    - the git commit and whether the working tree was dirty;
    - the dataset version and the SHA-256 of the window and label files;
    - the registry hash;
    - the forecaster and detector name and version, plus the detector configuration and its hash;
    - the matching-rule ADR and its version;
    - the seed, if any;
    - the architecture, the Python version and the lockfile hash;
    - the aggregated mock flag.
43. As the P6 owner, I want a run with mocks or provisional sensor IDs watermarked "NOT A RESULT", so that it cannot be cited.
44. As the P6 owner, I want every report headed "simulated data (BattLeDIM L-Town); not validated on a real network", so that no result is overclaimed.
45. As the P6 owner, I want a fast in-process evaluation mode for development, so that I can iterate on the metrics without a running server.
46. As the P6 owner, I want the official run to be computed from recorded run artefacts (every window response and every alert), not from the in-memory API afterwards, so that a restart or capacity limit cannot silently drop data.
47. As the P6 owner, I want a run invalidated if the backend restarted mid-run (its start time changed), so that a run with a reset CUSUM state is not reported.
48. As the P6 owner, I want a run's own artefacts (alerts and status) to be usable directly by the dashboard's playback adapter, so that the demo fallback shows a real recorded run.
49. As a reviewer, I want a change to the matching rule to require a new ADR and a new run reported next to the old one, so that results are never silently replaced.

### Cross-team

50. As the P6 owner, I want my missing endpoints filed as questions with owners and proposed shapes, so that I am a client stating needs and not the person implementing other modules. The questions are residuals over time, audit read, LLM explanation, alert lifecycle and a candidate pipe.
51. As the P3, P4 or P5 owner, I want each proposal to state what the dashboard does until it is answered, so that nothing blocks on me before the deadline.

## Implementation Decisions

### Scope tiers (deadline 2026-10-18 10:00)

- **Tier 1 (mandatory, depends only on P6):**
  - operator dashboard MVP on existing endpoints;
  - contract-validated fixtures;
  - playback adapter with banner;
  - CI label guard;
  - cross-team questions filed.
- **Tier 2 (only if the real detector is plugged into the VM API by 22:00 on 2026-10-17):**
  - matching-rule ADR (written before running);
  - in-process evaluation over 2019;
  - reduced manifest;
  - report with header and watermark rules.

  If the trigger is missed, Tier 2 is dropped and the time goes into Tier 1 polish and demo rehearsal.
- **Tier 3 (deferred, recorded as future work):**
  - browser end-to-end test;
  - recorded-artefact official run via the replay client;
  - full-year latency from client samples;
  - `until`-anchored pagination;
  - residuals, audit and explanation screens;
  - alert lifecycle;
  - official BattLeDIM score;
  - client-side hash-chain verification.

### Dashboard

- **Server-side Python dashboard (Streamlit).** It lives in the dashboard area and its dependency goes in an optional dependency group, so the VM runtime install is unaffected.
  - Primary deployment: an x86 laptop with an SSH tunnel to the VM API, as documented in the VM runbook.
  - Fallback if the tunnel or the install fails: plain HTML served from the API origin. That needs a P3 change.
- **The dashboard imports only the contract models from the backend package.** Never services, ports or models.
- **Data-source seam (new, the only one):** an alert source with operations equivalent to:
  - `list_alerts(limit, offset, since, until, region)` → `AlertPage`
  - `get_alert(alert_id)` → `Alert`
  - `status()` → `StatusResponse`
  - `health()` → `HealthResponse`

  Two adapters:
  - **HTTP:** base URL from configuration.
  - **Playback:** reads a recorded run (alerts as JSONL plus a status snapshot) and applies the same filtering, ordering and pagination semantics as the API.

  Both adapters return parsed contract models. A parse failure surfaces as a typed "contract mismatch" error, and an unreachable backend as a typed "unavailable" error.
- **Contract source of truth:** the Pydantic models in the shared schemas package. No parallel JSON-schema folder is created, and no TypeScript types (this supersedes the earlier idea of types generated from OpenAPI).
- **Clock:** "now" is the status response's last accepted timestamp. Alert age is that minus `window_end`. `hours_since_onset` is displayed as received. `created_at` appears only in the detail view, labelled.
- **"Recent alerts", not "active alerts":** the contract emits alerts only when they open and never closes them. When an alert state field exists (Q-DET-04), an "open" filter is added without changing the source seam.
- **Refresh:** polling every 5 s (configurable).
  - It queries alerts since the newest `window_end` already shown; `since` is inclusive, so results are deduplicated by `alert_id`.
  - On error it backs off exponentially up to 60 s.
  - Liveness comes from the health endpoint.
  - History pagination uses `offset` in the MVP. Anchoring on `until` is recorded as technical debt.
- **Labels:**
  - "SIMULATED" when `status.mocks_active` or `alert.is_mock`;
  - "RECORDED REPLAY — run <id>, <date>" whenever the playback adapter is active;
  - "provisional sensor IDs" when the registry is provisional.
- **Audit:** the alert shows `audit.seq`/`audit.hash` raw, or "audit unavailable" when null. The UI never shows a "verified" state. Independent verification waits for the audit read contract (Q-AUD-06), and the backend will not supply a verification boolean.
- **Explanation:** structured evidence only. The narrative slot shows "explanation unavailable" until Q-AUD-07 exists.
- **Labels never enter the dashboard.** A CI guard enforces this.

### Evaluation

- **Ownership:** P6 owns the evaluation module, the matching rule and the report. P4 owns detector calibration and reviews the matching rule. P6 never tunes detector parameters.
- **Data partition:**
  - 2018 is the only training and calibration year. P1's clean-mask policy "reparados" is the default.
  - 2019 is read only by evaluation.
  - The 2019 ground truth (`pipe`, `start`, `end`, `leak_size`, `type`, `peak`; 23 leaks) lives in the evaluation data area.
- **Matching rule (ADR, frozen before the first real-detector run).** The proposed default, to be confirmed in the ADR:
  - an alert matches a leak if its `window_end` falls in [`start`, min(`end`, `start` + Tmax)];
  - each leak matches at most its earliest alert;
  - extra alerts during the same leak are "duplicates", neither true nor false positives;
  - alerts matching no leak are false alarms;
  - the treatment of the 4 leaks active on 2019-01-01 is stated explicitly.

  Tmax and the duplicate policy are ADR decisions.
- **Metrics:**
  - detected, missed;
  - detection delay distribution (median, p90);
  - onset error, when `suspected_onset` is present;
  - false alarms per week.

  Region accuracy is "not computed" while `region` is null. The official BattLeDIM score is "not computed" unless P4 supplies a candidate pipe per alert and P1 supplies pipe-distance on the L-Town graph. Before any score is claimed, its formula, Tmax, Xmax (300 m along pipes) and costs are taken verbatim from the official rules and evaluation code.
- **Execution modes:**
  - **In-process (Tier 2):** builds the pipeline with the configured forecaster and detector and feeds the 2019 windows in order.
  - **Recorded-artefact (Tier 3):** reads `responses.jsonl` and `alerts.jsonl` produced by the replay client with a new `--responses-out` option, owned by P3/P1. A changed `started_at` invalidates the run.
- **Manifest:** written per run and embedded in the report (fields in story 42). Tier 2 ships a reduced manifest: commit, model infos, detector config hash, data hashes, mock flag, architecture.
- **Report header and watermark** rules as in stories 43–44.
- **Playback compatibility:** an evaluation run emits alerts and a status snapshot in the playback adapter's format.

### Cross-team proposals (filed as questions, not implemented by P6)

| Question | Owners | Request |
|---|---|---|
| Q-UI-05 | P3 + P4 | `GET /v1/residuals` returning, per scored window, `observed_m`, `expected_m`, `residual_m`, `z` and `cusum` for each sensor; additive v1.1 |
| Q-AUD-06 | P5 + P3, supersedes Q-AUD-04 | Audit read contract: `GET /v1/audit?after_seq&limit` and `/v1/audit/head`; canonical JSON spec; genesis `prev_hash`; exact hash rule; no backend `verified` flag |
| Q-AUD-07 | P5 + P3 | `GET /v1/alerts/{id}/explanation` with status (ready/pending/unavailable/disabled), generator ModelInfo and `alert_payload_sha256` |
| Q-DET-04 (raised priority) | P4 → P3 | Alert lifecycle: `state`, `closed_at`, `last_seen_window_end` |
| Q-DET-06 | P4 | Candidate pipe (link ID) per alert |
| Q-UI-04 (reworded) | P3 / P1 | Replay client records every response; evaluation reads run artefacts |
| Q-UI-03 | — | Resolved: no CORS needed (server-side dashboard) |
| Doc drift | P3 | `not_applicable` audit status is missing from contract §1 |

### ADRs

- **ADR — Dashboard architecture:** server-side Python, runtime contract parsing, HTTP/playback source seam, mandatory banners, simulated clock, polling.
- **ADR — Evaluation protocol:** partition, label isolation, matching rule, metric names, manifest, watermark, BattLeDIM-score policy. Must be `Accepted` before the first 2019 run with a real detector.

## Testing Decisions

- **What a good test is here:** it drives a module through its public seam and asserts observable output (rendered text and states, metric values, manifest fields). It never asserts internal calls or widget structure. A good test survives a refactor of the internals.
- **Seams under test (two, one per module):**
  1. **Dashboard:** the app is run headless with the playback adapter (Streamlit's app-testing harness). Fixture runs cover:
     - normal alerts;
     - every nullable field null;
     - an empty deviating-sensor list;
     - `is_mock=true`;
     - audit null;
     - an empty run;
     - a malformed record (expects "contract mismatch");
     - an unreachable HTTP backend (expects "API unavailable").

     Assertions cover the visible banners (SIMULATED, RECORDED REPLAY), the simulated-clock header, deduplication across refreshes and the empty, loading and error states.
  2. **Evaluation:** a pure function from (alerts, leaks, matching parameters) to metrics, tested with small hand-made tables:
     - an alert before the start, inside the window, after Tmax;
     - two alerts for one leak;
     - an alert with no leak;
     - a leak active at year start;
     - `suspected_onset` null.

     Further tests check that the manifest contains the required fields, that mock or provisional runs carry the watermark, and that the header is always present.
- **Contract tests:** every fixture and playback file is parsed by the contract models inside the normal test suite, so `just check` and CI catch drift.
- **Guard test:** the dashboard sources contain no references to leak labels or ground-truth paths.
- **Prior art:** contract example validation tests, the synthetic end-to-end replay test (seeded generator with injected leak, plumbing-only assertions) and the API tests using FastAPI's test client. The P1 test that fails if 2019 label files appear in the data area is prior art for the label guard.
- **Not tested by P6:** forecaster, detector, audit sink and narrative internals; each owner runs the existing port-level tests with their class. No test asserts detection quality on synthetic data; detection quality is stated only by the evaluation report.

## Out of Scope

- Any screen backed by an endpoint that does not exist: residuals over time, audit history, chain verification, LLM narrative.
- Alert open/closed lifecycle and any front-end heuristic for "active".
- Official BattLeDIM score, pipe-level localisation and region accuracy (no region yet).
- Browser end-to-end test, SSE/WebSocket, CORS, authentication.
- Changing contracts, the replay client or other owners' modules. These go through questions and pull requests by their owners.
- Calibrating or tuning the detector or forecaster.
- Any claim of validation on a real network.

## Further Notes

- **Data facts relied on:**
  - 2019 has 105,120 windows and 0 NaN; the full 2019 HTTP replay passes validation with 0 rejections (mocks).
  - Official sensor IDs are in use.
  - With the mocks on real 2019 data, 0 alerts are produced. The demo needs the synthetic replay or a real detector to show an alert.
- **Status latency caveat:** `/v1/status` percentiles cover only the last 10,000 samples per stage, so they are not full-year numbers.
- **Day-of contingency:**

  | Failure | Fallback | What the screen or speech says |
  |---|---|---|
  | VM down | API on x86, same commit | s390x numbers quoted from the recorded baseline, with date |
  | GRU fails | A real secondary forecaster if P2 provides one | Never the mock presented as a model |
  | No real model | Playback of a recorded run | Banner on |
  | LLM down | Structured evidence | Unchanged |
  | Audit down | Fail-closed behaviour per current backend | Alert shows audit unavailable or pending |

- **Checkpoints:**
  - C1: dashboard vs mocks green.
  - C2: real IDs smoke. Already satisfied by the backend.
  - C3: real models plugged, `mocks_active=false`.
  - C4: evaluation ADR accepted, then run.
  - C5: demo rehearsed twice, once with forced fallback.
- The decisions trace back to the grilling session of 2026-10-17 (Q1–Q14).
