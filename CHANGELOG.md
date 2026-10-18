# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow SemVer;
contract versions are tracked separately (`schema_version`).

## [Unreleased]

### Changed
- BE-022: align the operator workspace with the public CivicWater Figma palette and IBM Plex
  typography, keep issued alerts first, and show the source-derived L-Town map with selected
  alert evidence. On narrow screens the map scrolls internally at readable label size;
  unknown regions and candidate pipes are reported. Existing alert details, history,
  metrics and mock/replay notices remain available (ADR-012).
- BE-021: refresh the Streamlit dashboard with a responsive operator workspace, section
  navigation, local Geist/Phosphor assets, source-derived pipeline/buffer/sensor diagrams,
  collapsible exact evidence and reduced-motion-aware local GSAP reveals. Add a manual
  refresh action that clears polling back-off. Prioritise issued-alert summaries, enlarge
  alert text to 16/14 px and move technical metrics to a disclosure while retaining all
  loaded records. Improve heading order, explicit-action focus, contextual count semantics,
  text/chart contrast and enlarged-text reflow following the requested UX skill review.
  Simulation/replay notices, source contracts, automatic refresh and history filters are
  retained (`docs/dashboard-design.md`, `docs/dashboard-ux-review.md`).
- `Alert` contract 1.1 (additive, Q-DET-07): optional `candidate_pipe` (also on `AlertCandidate`),
  `null` without a pipe network; new alerts carry `schema_version: "1.1"`, 1.0 payloads are still
  accepted. `docs/examples/alert.mock.json` updated. The alert JSON gained a key, so the
  determinism digest of `tests/detection/test_determinism.py` was regenerated
  (`7c5ef97b…`); the previous one (`d9106c15…`) is the same replay without that key.
- One sensor-ID scheme: the official registry `config/sensors.battledim.json` (data owner);
  loading `data/vN/sensors.csv` as a registry was removed (it used CSV column names such as
  `Pressure_n1`, incompatible with the replay). Registry JSON may carry optional `units`.
- Audit is fail-closed (team decision Q-AUD-03): alerts are served only after their audit
  event is recorded; failed events wait in an ordered pending queue retried before each window;
  503 `audit_backlog_full` when `SENTINEL_AUDIT_PENDING_CAPACITY` is reached. Window `audit`
  is now `recorded | pending | disabled`; `/v1/status` reports the backlog.
- z sign convention (Q-DET-02): `z = (expected − observed)/σ`, positive on a pressure drop;
  `deviation_m` stays observed − expected. Mock detector and examples updated. The CUSUM
  accumulates this `z` as is (ADR-007 §2 corrected: no `d = −z`).
- Timestamps without timezone are read as UTC (`SENTINEL_NAIVE_TIMESTAMP_TZ=UTC`, default) and
  flagged with `timestamp_tz_assumed` in the result and audit event; `reject` restores the 422.
  The HTTP input model is now `SensorWindowIn`; `SensorWindow` stays timezone-aware.

### Added
- BE-022: `config/l-town-map.json` from the official BattLeDIM topology (attributed CC BY 4.0),
  a pure SVG renderer, a dedicated Streamlit map page with SVG download, a Jupyter selection
  view and a notebook. The official source hash matches the prompt; the generated map hash
  differs from its expected digest and is documented in `docs/dashboard-design.md`.
- Windows verification compatibility: keep the hash-pinned regions JSON at LF on checkout,
  and normalise paths in the evaluation-boundary guard. No region data or runtime code changed.
- BE-011: the API loads the real forecaster at startup. `SENTINEL_FORECASTER` accepts
  `mock | baseline | xgboost | gru` with `SENTINEL_FORECASTER_PATH`; XGBoost options
  `SENTINEL_FORECASTER_CONTRIBUTIONS` (TreeSHAP on/off), `_ENGINE`, `_TREE_FORMAT`
  (`auto | compress_trees | zdnn_tensors`, checked at startup). A load error stops startup (no
  fallback to the mock); the CUSUM detector is checked against the forecaster actually loaded.
  `scripts/shared_api.sh` forwards the `SENTINEL_FORECASTER*` and `SENTINEL_DETECTOR*` variables
  that are set; `scripts/validate_replay_ingestion.py` reads them and reports processing issues.
- Pipe network, L-Town regions and candidate pipe (BE-012g, BE-012i, ADR-011): `PipeNetwork`
  (`sentinel.detection.network`: distances along the pipes, zones, `candidate_pipe`), the builder
  `python -m sentinel.detection.offline.topology` (with `--check`) and the committed
  `config/regions.battledim.json` (nine regions over the 33 official pressure sensors, a rule fixed
  from the topology alone). The network is the data owner's `l-town.json` (schema 1.0, in
  `$SENTINEL_DATA_DIR/network/v1/`, never in git): `SENTINEL_DETECTOR_NETWORK` (optional, with
  `SENTINEL_DETECTOR=cusum`) points to it. `scripts/shared_api.sh` forwards it to the container and
  `docs/runbook-vm.md` has the check to run on the VM.
  Answers Q-DET-05 and Q-DET-07 (confirmation asked in Q-DET-08); DEP-DET-04 resolved, DEP-DET-06
  partial. New example `docs/examples/alert.cusum-synthetic.json`.
- Operator dashboard tracer (UI-001, P6): Streamlit page (`just dashboard api_url=...`) with
  the header from `/health` + `/v1/status` (simulated time, models, SIMULATED badge). Reads the
  backend only through `dashboard.source.AlertSource` (HTTP adapter, stdlib); every answer is
  parsed with the contract models (`ContractMismatchError` / `SourceUnavailableError`).
  Streamlit is in the optional `dashboard` dependency group (CI installs it; the runtime install
  is unchanged). Guard tests: no leak-label references in `dashboard/`, and the dashboard
  imports only `sentinel.schemas` from the backend.
- Dashboard playback (UI-002, P6): `PlaybackAlertSource` serves a recorded run (`alerts.jsonl`
  in stored order + `status.json` + optional `run.json`) with the API's list semantics, behind
  the same `AlertSource` seam; `SENTINEL_DASHBOARD_PLAYBACK_DIR` / `just dashboard-playback`
  switch to it and the page shows a permanent "RECORDED REPLAY — run <id>, <date>" banner and
  "recorded replay" instead of the API state. Fixture runs (`fixture-edge-cases`,
  `fixture-empty`, `fixture-malformed`) generated from the mock backend by
  `scripts/make_playback_fixtures.py` (`just playback-fixtures`). `AlertQuery` enforces the
  API bounds (`limit` 1–500, `offset` ≥ 0).
- Dashboard "Recent alerts" (UI-003, P6): table newest first with alert ID, window end, region
  ("region not available"), deviating-sensor count, strongest deviation and its z ("not
  provided" when null), estimated onset, age on the simulated clock (`status.last_timestamp`
  minus `window_end`) and a per-alert SIMULATED badge; empty state, loading spinner and a
  contract-mismatch state for alerts. Works with the HTTP and playback sources.
- Dashboard alert detail (UI-004, P6): select an alert of the list to see its evidence exactly
  as received (deviating sensors, CUSUM vs threshold, onset, contributions, summary, models),
  the audit reference raw or "audit unavailable" (never "verified"), "explanation
  unavailable", and `created_at` labelled "recorded by server".
- Dashboard polling and history (UI-005, P6): the list refreshes every
  `SENTINEL_DASHBOARD_REFRESH_S` (5 s) with `since` + dedup by `alert_id`; "API unavailable
  since hh:mm" with back-off up to 60 s; contract mismatch hides the data; the page reruns when
  the simulated time or the alerts change; warning when more than 500 alerts arrive in one
  poll. Paginated history with time-range and region filters.
- Jupyter view of the dashboard (P6): `dashboard/jupyter.py` (ipywidgets) + notebook
  `notebooks/p6_eval/dashboard.ipynb`; same source seam, poll, formatting and banners as the
  Streamlit page, for JupyterLab on the VM where Streamlit cannot run (no `pyarrow` for s390x).
  `ipywidgets` added to the optional `dashboard` group. Checked in the VM's container (s390x).
- Dashboard checked against the real CUSUM detector (P6): playback run `fixture-cusum` generated
  by `CusumDetector` with an invented synthetic calibration and region mapping (`is_mock=true`)
  fills `region`, `cusum`, `since` and an onset before the alert; 4 headless tests. The
  SIMULATED banner now says "a mock model or a non-final calibration".
- ADR-010 (layer-2 evaluation, `Proposed`, answers Q-UI-06): official BattLeDIM time window
  `[start, end]` (scoring code read on 2026-10-17: no Tmax, 300 m pipe rule, repeats 0), all 23
  labelled leaks (`max_delay` 731 days so the 4 inherited leaks keep `[start, end]`),
  `layer1.evaluate` reused, secondary views "within 7 days" and "new in 2019"; false alarms only
  from the 7-day view (0 by construction under the official window without the pipe rule);
  region accuracy and euro score "not computed" until regions/pipes exist.
- Layer-2 run (EV-003, P6): `python -m sentinel.eval --windows … --api … --labels … --out …`
  checks the labels against the official BattLeDIM 2019 list before posting (`sentinel.eval.labels`),
  replays through the API recording every response (`sentinel.eval.run`: fresh-instance check,
  restart detection with 1 s tolerance, rejections by HTTP code), fetches the alerts, and writes
  playback-compatible artefacts, `report.md` and `manifest.json` (calibration hash required and
  checked for the CUSUM; missing alerts watermark the report).
- Layer-2 evaluation core (EV-002, P6): `sentinel.eval.metrics.evaluate_run` (ADR-010 views over
  `layer1.evaluate`: primary official window with `max_delay` 731 days and a guard against longer
  label windows, 7-day operator view as the only false-alarm source, leaks new in the year;
  delays from start and peak, onset error) and `sentinel.eval.report.render_report` (wording
  header, NOT A RESULT for mocks / provisional IDs / dirty tree, per-leak table, manifest).
- Demo runbook (UI-007, P6): `docs/demo-runbook.md` — setup with a free local tunnel port,
  timed script, banner meanings, contingency; history empty state now reads "No alerts match the
  history filters" (it repeated the list's text).
- Merge repair: Q-UI-05 and Q-UI-06 (detection owner) and the P6 proposal in Q-DET-04, dropped
  by the merge of `main`, restored; testing.md sentence on data-reading tests restored.
- ADR-009 (dashboard, `Proposed`) and P6 cross-team questions Q-UI-07, Q-AUD-06, Q-AUD-07,
  Q-DET-07, Q-DOC-01/02; Q-DET-04 lifecycle fields proposed; DEP-UI-03, DEP-DET-06 added
  (UI-006). ADR-007/008 and Q-UI-05/06, Q-DET-06 belong to the detection owner (BE-012).
- CUSUM detector `CusumDetector` (BE-012a..e; ADR-007/008, contract §3): per-sensor `S` capped at
  `c·h`; alert episodes per sensor (claim, release below `h_close`, no reset);
  `normal`/`suspect`/`alarm`; onset (`suspected_onset` null when unknown); decay of `S` across
  missing windows (`skipped_windows`, `reset_reason="gap"`); atomic rejection with
  `DetectionError`; calibration artefact with provenance, forecaster identity and sha256
  (`calibrate()`, `write_artefact`, `CusumDetector.for_pipeline` with `ConfigError` on a
  registry/order/forecaster mismatch; `is_mock` unless `provenance = battledim-2018-oof`);
  optional sensor → region mapping (`region`, `region_scores`, `diagnostics.regions_id`).
  Tested with invented calibrations. Determinism digest for the s390x parity run (BE-015) in
  `tests/detection/test_determinism.py`.
- Startup selection of the CUSUM detector (BE-012): `SENTINEL_DETECTOR=cusum` with
  `SENTINEL_DETECTOR_CALIBRATION` (required with `cusum`) and `SENTINEL_DETECTOR_REGIONS`
  (optional). `build_pipeline` calls `CusumDetector.for_pipeline` with the forecaster actually
  served; a missing variable, a missing or invalid file, a calibration of another forecaster,
  other or reordered sensors, a mapping that does not cover the registry, and either file set
  with the mock detector are a `ConfigError` before the server listens. `mock` stays the
  default; the detector is `is_mock=true` unless the calibration provenance is
  `battledim-2018-oof` (none exists yet). New settings fields `detector_calibration_path` and
  `detector_regions_path`; tests in `tests/test_detector_startup.py` and `tests/test_config.py`;
  usage in the README ("Running with the CUSUM detector").
- Offline tools of the CUSUM calibration (BE-012f, development only, never imported by the
  server; pandas is a dev dependency) in `src/sentinel/detection/offline/`: `baseline.py`
  (out-of-fold hour-of-week residuals with monthly folds, reproducible residual and fold files
  with their hashes, 2018 loader checked against `MANIFEST.json`), `sweep.py` (`replay` with exact
  parity with `CusumDetector` and a parameter `sweep`) and `layer1.py` (delay and false-alarm
  counts; the matching rule is a required argument and provisional, Q-UI-06). The sweep showed that
  false alarms are not monotone in `h` (ADR-008 §E). BE-012f is not finished: no artefact has
  been produced from a run yet.
- Documentation: end-to-end check of the CUSUM selection against the real server with synthetic
  data (`docs/testing.md`) and its timings (`docs/performance.md`); ADR-007/008, contract §3,
  architecture, dependencies and plan updated. No schema change.
- Forecasting (ML owner): hour-of-week baseline, per-sensor XGBoost served with Snap ML (with
  TreeSHAP contributions) and a 33-sensor GRU (TensorFlow, ONNX export with a fused `GRU` node);
  shared feature definitions, `load_forecaster` registry, training helpers
  (`forecasting/training.py`, P1's clean mask), notebooks in `notebooks/p2_forecast/` and
  `docs/p2-forecasting.md`. Forecasters accept both sensor ID spellings (`n1` / `Pressure_n1`). Snap ML import of XGBoost 3.x JSON fixed
  (`portable_booster_json`). ML tests skip where numpy/xgboost/tensorflow are absent.
  Validation reports MAE on clean windows and a one-sided (pressure-drop) leak contrast
  (`metrics_version` 2); sensors with σ = 0 are left out of the contrast.
  XGBoost chosen and measured on the LinuxONE VM (`docs/performance.md`); `load_forecaster`
  takes `engine`, `tree_format` and `with_contributions` for it.
- `scripts/validate_replay_ingestion.py`: runs a replay JSONL (e.g. the data owner's
  `windows_2019.jsonl`) through the pipeline in process.
- `scripts/shared_api.sh` and `docs/runbook-vm.md`: the shared integration API on the VM
  (running on port 8000 with the full 2019 replay loaded, mocks).
- First LinuxONE (s390x) baseline: tests, benchmark, HTTP replay and x86/s390x hash parity
  (`docs/performance.md`).
- GitHub Actions CI running `just ci` on pull requests and pushes to `main`.
- Contracts v1.0 (`Proposed`): window, forecast request/result, detection result, alert,
  audit event/ref; examples in `docs/examples/` validated by tests.
- Provisional sensor registry with placeholder IDs (33 pressure, 3 flow, 1 tank level).
- In-memory history buffer (default 288 past windows, scoring after 144 contiguous).
- Pipeline with temporal policy (duplicate → 200, conflict/out-of-order → 409, gaps
  reported), failure containment, idempotent resend, deterministic alert IDs, audit events.
- Ports `Forecaster`, `Detector`, `AuditSink` with labelled mocks
  (`mock-persistence`, `mock-threshold`, null / in-memory audit).
- HTTP API: `GET /health`, `POST /v1/windows`, `GET /v1/status`, `GET /v1/alerts`,
  `GET /v1/alerts/{alert_id}`, uniform error envelope.
- Per-stage latency metrics, counters, throughput, error rate; JSON-lines logs.
- Scripts: synthetic generator, replay client, in-process benchmark.
- ADR-001..006, implementation plan, dependencies, questions for the team, testing and
  performance docs, `AGENTS.md`, `CONTRIBUTING.md`.
- Tooling: uv, ruff, mypy (strict), pytest, justfile.
- Detector design (`Proposed`): ADR-007 (CUSUM statistic, per-sensor alert episodes,
  gap decay, atomicity, regions), ADR-008 (calibration and evaluation protocol), contract §3
  semantics, DEP-DET-05, BE-012 split into vertical slices BE-012a..h (BE-012a is a tracer
  bullet), questions Q-TEAM-03, Q-ML-07, Q-ML-08, Q-UI-05, Q-UI-06, Q-DET-06. No schema change.

## [0.0.0] — 2026-10-17

- Repository bootstrap.
