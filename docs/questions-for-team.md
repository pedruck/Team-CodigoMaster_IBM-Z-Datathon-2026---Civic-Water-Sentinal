# Questions for the team

Each question proposes a default that is **already implemented**. Answer "OK" or give the
alternative; the backend owner then updates the contract/ADR and closes the question.
Priority: **P0** blocks real data or models, **P1** blocks a correct evaluation, **P2** polish.

## Team

| ID | P | To | Question | Proposed default |
|---|---|---|---|---|
| Q-TEAM-01 | P0 | all | Who owns each role (data & replay, ML forecasting, detection & localisation, audit & narrative, dashboard & evaluation, LinuxONE platform)? | none — fill `docs/dependencies.md` owners |
| Q-TEAM-02 | P0 | all | Will the final submission be one repository? | Keep this repo for backend now; move it as `backend/` into the team repo with `git subtree` (keeps history) when it exists |

## Data & replay

| ID | P | Question | Proposed default (implemented) | Why it matters |
|---|---|---|---|---|
| Q-DATA-01 | P0 | Units of pressure, flow and tank level in the exported SCADA series? | **Answered by `data/v1/sensors.csv`:** pressure m, flow m³/h, tank level m | alert evidence is in metres (`deviation_m`) |
| Q-DATA-02 | P1 | v1 timestamps are naive (`2019-01-01 00:00:00`), on an exact 300 s grid, without DST artefacts. Confirm they are UTC and mark the **end** of the interval | backend reads naive timestamps as UTC (implemented; the replay can send the CSV value as is) | if the export were local time, every window would be shifted |
| Q-DATA-03 | P0 | Are there missing timestamps or NaN values in 2018/2019? How many? | **Answered by profiling `data/v1`:** none (105 120 rows/year, 0 gaps, 0 NaN) | one gap blinds the system for `min_history` windows |
| Q-DATA-04 | P1 | Can the replay emit JSONL in the window contract (one window per line, in order)? | `scripts/replay.py` consumes exactly that | avoids a second format |
| Q-DATA-05 | P1 | Confirm labels (leak start/pipe) never enter the API and live only in the evaluation step | unknown fields are rejected | prevents label leakage |
| Q-DATA-06 | P0 | Official sensor IDs (33 + 3 + 1) and their canonical order | **Answered by `data/v1/sensors.csv`;** registry order = file order = array order | forecaster arrays and region mapping depend on it |

## ML forecasting

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-ML-01 | P0 | Accept the `Forecaster` contract (§2): one call per window, `ForecastRequest` → `ForecastResult` for all 33 sensors? | as implemented | integration without backend changes |
| Q-ML-02 | P0 | Does XGBoost use the **other** sensors' current pressures (design doc §3)? If yes, during a leak those sensors also drop and pull the forecast down — is that acceptable? | allowed; must not use the target sensor's own value | leakage / masking of leaks |
| Q-ML-03 | P1 | Artefact format and loading: XGBoost JSON → Snap ML; GRU ONNX → onnxruntime or zDLC; loaded once at startup? | load at startup, fail fast | startup behaviour |
| Q-ML-04 | P1 | Which runtime will run on the VM (zDLC vs PyTorch container)? Same process as FastAPI? | same process; out-of-process adapter only if needed | ADR-005 |
| Q-ML-05 | P1 | Expected inference latency per window and warm-up needs | none assumed | p99 budget |
| Q-ML-06 | P2 | `min_history_windows` per model (baseline 0? XGBoost 1? GRU 144/288?) | backend minimum 144 | when scoring starts |

## Detection & localisation

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-DET-01 | P0 | Accept the `Detector` contract (§3), stateful single instance, atomic `evaluate`? | as implemented | CUSUM state correctness |
| Q-DET-02 | P0 | Sign convention: design doc §3 says `z = (expected − observed)/σ`, but the §6 example has `deviation_m −0.9` with `z −4.1`. Which one? | **Decided (team-workflow):** `deviation_m = observed − expected` (< 0 on a drop), `z = (expected − observed)/σ` (> 0 on a drop) — implemented | alert evidence consistency |
| Q-DET-03 | P1 | What should CUSUM do after a gap or a restart: reset or continue? | backend sends nothing special; detector sees non-consecutive timestamps | false alarms / missed leaks after gaps |
| Q-DET-04 | P1 | Alert lifecycle: does an alert ever close (S falls below h)? Re-open as new alert? | only "opened" alerts are emitted; no closing | dashboard "open alerts" view |
| Q-DET-05 | P1 | Region IDs and sensor → region mapping location | owned by detector; `region` null until then | `/v1/alerts?region=` |

## Audit & narrative

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-AUD-01 | P0 | Accept the `AuditSink` port and event types (`window.scored`, `window.not_scored`, `alert.raised`)? | as implemented | integration |
| Q-AUD-02 | P1 | Persistence of the log (file/DB) and its location on the VM | not implemented here | durability |
| Q-AUD-03 | P0 | If the audit append fails: keep the alert (fail-open) or reject/stop (fail-closed)? | **Decided (team-workflow): fail-closed** — alert withheld until audited, events queued, 503 when the queue is full — implemented (ADR-006) | availability vs traceability |
| Q-AUD-04 | P2 | Is a `GET /v1/audit` read endpoint needed, for whom, with what auth? | not created | scope |
| Q-AUD-05 | P1 | Circularity in design doc §6: the alert carries `audit_seq` and the record carries `payload_sha256` of the alert. Hash the alert **without** the audit field? | **Decided (team-workflow):** yes (`exclude={"audit"}`) — implemented | verifiable chain |

## Dashboard & evaluation

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-UI-01 | P1 | Are the filters (`since`, `until`, `region`, `limit≤500`, `offset`) enough? | as implemented | API scope |
| Q-UI-02 | P1 | Status vocabulary to display: `scored`, `insufficient_history`, `forecast_failed`, `detection_failed`, `audit` flag, `is_mock` | as implemented | operator trust |
| Q-UI-03 | P2 | Is the dashboard served from another origin (needs CORS)? | no CORS | browser access |
| Q-UI-04 | P1 | Evaluation reads alerts from the API after the replay, or from the audit log? | from the API (`GET /v1/alerts`) | reproducibility |

## Platform

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-PLAT-01 | P0 | When is the LinuxONE VM available, which Python, can we `uv sync` there? | Answered 2026-10-17: available, Python 3.12.3; uv 0.11.16 installed for `linux1` | ADR-001 risk |
| Q-PLAT-02 | P1 | May we `sudo apt install gcc python3-dev` on the shared VM? It lets `mypy` (via `librt`) and any wheel-less package build there; the model runtimes may need it too | install it (owner of the VM decides) | full `just check` on s390x |
