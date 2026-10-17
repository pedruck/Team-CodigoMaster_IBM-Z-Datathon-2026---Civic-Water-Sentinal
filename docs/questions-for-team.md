# Questions for the team

Each question proposes a default that is **already implemented**. Answer "OK" or give the
alternative; the backend owner then updates the contract/ADR and closes the question.
Priority: **P0** blocks real data or models, **P1** blocks a correct evaluation, **P2** polish.

Exception: the cross-team proposals filed by the dashboard & evaluation owner (P6) —
Q-UI-07, Q-AUD-06, Q-AUD-07, Q-DET-07, Q-DOC-01, Q-DOC-02 — ask other owners for something
that does **not** exist yet. Their "Proposed default" is a proposed shape, not an
implementation; the last column says what the dashboard does until the question is answered
(ADR-009). Owners are named P1–P6 as in `docs/team-workflow.md`.

## Team

| ID | P | To | Question | Proposed default |
|---|---|---|---|---|
| Q-TEAM-01 | P0 | all | Who owns each role (data & replay, ML forecasting, detection & localisation, audit & narrative, dashboard & evaluation, LinuxONE platform)? | none — fill `docs/dependencies.md` owners |
| Q-TEAM-02 | P0 | all | Will the final submission be one repository? | Keep this repo for backend now; move it as `backend/` into the team repo with `git subtree` (keeps history) when it exists |
| Q-TEAM-03 | P1 | all | What false-alarm budget (alerts per week) defines the detector's operating point? | detector owner proposes one point of the 2018 out-of-fold delay × false-alarms curve; the team approves it and it is recorded (ADR-008) before the single 2019 run |

## Data & replay

| ID | P | Question | Proposed default (implemented) | Why it matters |
|---|---|---|---|---|
| Q-DATA-01 | P0 | Units of pressure, flow and tank level in the exported SCADA series? | **Answered (`docs/data.md`):** pressure m, flow m³/h, tank level m | alert evidence is in metres (`deviation_m`) |
| Q-DATA-02 | P1 | v1 timestamps are naive, on an exact 300 s grid, without DST artefacts. UTC and end of interval? | **Answered (`docs/data.md`):** BattLeDIM has no timezone; simulated time is UTC by convention; timestamp = end of the 5-min window. The replay writes `Z`; the backend also reads naive timestamps as UTC | — |
| Q-DATA-03 | P0 | Are there missing timestamps or NaN values in 2018/2019? How many? | **Answered by profiling `data/v1`:** none (105 120 rows/year, 0 gaps, 0 NaN) | one gap blinds the system for `min_history` windows |
| Q-DATA-04 | P1 | Can the replay emit JSONL in the window contract (one window per line, in order)? | `scripts/replay.py` consumes exactly that | avoids a second format |
| Q-DATA-05 | P1 | Confirm labels (leak start/pipe) never enter the API and live only in the evaluation step | unknown fields are rejected | prevents label leakage |
| Q-DATA-06 | P0 | Official sensor IDs (33 + 3 + 1) and their canonical order | **Answered:** `config/sensors.battledim.json` (network-element IDs `n1`…, `p227`, `PUMP_1`, `T1`); registry order = array order | forecaster arrays and region mapping depend on it |

## ML forecasting

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-ML-01 | P0 | Accept the `Forecaster` contract (§2): one call per window, `ForecastRequest` → `ForecastResult` for all 33 sensors? | as implemented | integration without backend changes |
| Q-ML-02 | P0 | Does XGBoost use the **other** sensors' current pressures (design doc §3)? If yes, during a leak those sensors also drop and pull the forecast down — is that acceptable? | allowed; must not use the target sensor's own value | leakage / masking of leaks |
| Q-ML-03 | P1 | Artefact format and loading: XGBoost JSON → Snap ML; GRU ONNX → onnxruntime or zDLC; loaded once at startup? | load at startup, fail fast | startup behaviour |
| Q-ML-04 | P1 | Which runtime will run on the VM (zDLC vs PyTorch container)? Same process as FastAPI? | same process; out-of-process adapter only if needed | ADR-005 |
| Q-ML-05 | P1 | Expected inference latency per window and warm-up needs | none assumed | p99 budget |
| Q-ML-06 | P2 | `min_history_windows` per model (baseline 0? XGBoost 1? GRU 144/288?) | backend minimum 144 | when scoring starts |
| Q-ML-07 | P1 | For each forecaster, which observed readings does the expected value depend on (the target sensor's own recent pressures, other sensors' current pressures, flows, tank level)? Can XGBoost ship in two variants, with and without the other sensors' current pressures (Q-ML-02)? | declared per model; both variants compared on the 2018 delay × false-alarm curve (ADR-008); the detector owner's prior is "without", the ML owner decides | CUSUM needs an expected value that does not follow the leak (ADR-007 §1); neighbours can mask a leak |
| Q-ML-08 | P0 | Can the hour-of-week baseline (design doc Table 2, step 1) be the first forecaster delivered, with out-of-fold 2018 residuals (time-block folds, the month of each labelled leak held out)? | yes; file format in DEP-DET-05 | the detector's final σ, k, h, N cannot be calibrated without real out-of-fold residuals |

## Detection & localisation

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-DET-01 | P0 | Accept the `Detector` contract (§3), stateful single instance, atomic `evaluate`? | as implemented | CUSUM state correctness |
| Q-DET-02 | P0 | Sign convention: design doc §3 says `z = (expected − observed)/σ`, but the §6 example has `deviation_m −0.9` with `z −4.1`. Which one? | **Decided (team-workflow):** `deviation_m = observed − expected` (< 0 on a drop), `z = (expected − observed)/σ` (> 0 on a drop) — implemented; the CUSUM accumulates `z` as is (ADR-007 §2) | alert evidence consistency |
| Q-DET-03 | P1 | What should CUSUM do after a gap or a restart: reset or continue? | backend sends nothing special; detector sees non-consecutive timestamps; each missing window counts as zero residual and `S` decays by `k` (ADR-007, implemented in `CusumDetector`) | false alarms / missed leaks after gaps |
| Q-DET-04 | P1 | Alert lifecycle: does an alert ever close (S falls below h)? Re-open as new alert? | only "opened" alerts are emitted; no closing; per-sensor episodes with a cap and hysteresis, no reset (ADR-007, implemented in `CusumDetector`); dashboard visibility in Q-UI-05 | dashboard "open alerts" view |
| Q-DET-05 | P1 | Region IDs and sensor → region mapping location | owned by detector; `region` null until then. Format fixed in ADR-007 §8: `{"regions": {"<region>": ["<sensor>", ...]}}`, file order breaks ties, startup fails unless it covers exactly `registry.pressure`; the L-Town content waits for BE-012g | `/v1/alerts?region=` |
| Q-DET-06 | P1 | Onset of a new episode on a sensor whose `S` has not returned to 0: ADR-007 §6 takes `since` = last window with `S = 0`, so a new episode that starts before `S` falls to 0 **inherits the old onset** (`suspected_onset` can be days before the new leak). Keep that literal rule, or restart `since` when the episode is released (`S < h_close`)? | keep the literal rule (implemented, `test_new_episode_keeps_the_last_zero_when_cusum_did_not_return_to_zero`); revisit with the layer-1 delay numbers | wrong `hours_since_onset` on repeated leaks of the same sensor |

## Audit & narrative

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-AUD-01 | P0 | Accept the `AuditSink` port and event types (`window.scored`, `window.not_scored`, `alert.raised`)? | as implemented | integration |
| Q-AUD-02 | P1 | Persistence of the log (file/DB) and its location on the VM | not implemented here | durability |
| Q-AUD-03 | P0 | If the audit append fails: keep the alert (fail-open) or reject/stop (fail-closed)? | **Decided (team-workflow): fail-closed** — alert withheld until audited, events queued, 503 when the queue is full — implemented (ADR-006) | availability vs traceability |
| Q-AUD-04 | P2 | ~~Is a `GET /v1/audit` read endpoint needed, for whom, with what auth?~~ **Superseded by Q-AUD-06** (the dashboard and the evaluation need it; proposed shape there) | not created | scope |
| Q-AUD-05 | P1 | Circularity in design doc §6: the alert carries `audit_seq` and the record carries `payload_sha256` of the alert. Hash the alert **without** the audit field? | **Decided (team-workflow):** yes (`exclude={"audit"}`) — implemented | verifiable chain |
| Q-AUD-06 | P1 | **To P5 + P3.** Read contract for the audit log, so anyone can verify the chain independently of the backend. P5's branch `leo/local-version` already has the chain format (`docs/audit-and-narrative.md`, record v1.0) and a CLI verifier (`python -m sentinel.audit.verify <chain.jsonl> [--alert alert.json] [--anchor-seq --anchor-hash]`); can they be exposed **read-only** over HTTP? Supersedes Q-AUD-04 | `GET /v1/audit?after_seq&limit` → records in `seq` order, exactly as stored; `GET /v1/audit/head` → `{seq, hash}`; the contract states the canonical JSON rule (today on P5's branch: `json.dumps(sort_keys=True, separators=(",", ":"), allow_nan=False)`, ASCII), the genesis `prev_hash` (64 zeros there) and the exact hash rule (`SHA-256(prev_hash ‖ canonical(record without prev_hash, hash))` there); **no** `verified` boolean from the backend — verification is the client's job; read-only, same (no) auth as the other read endpoints | **Until answered:** the dashboard shows `audit.seq` / `audit.hash` raw or "audit unavailable", never "verified"; no audit screen |
| Q-AUD-07 | P1 | **To P5 + P3.** HTTP access to the alert explanation. P5's narrative module (watsonx.ai Granite with a deterministic fallback, branch `leo/local-version`) has no HTTP endpoint | `GET /v1/alerts/{id}/explanation` → `{status: ready \| pending \| unavailable \| disabled, text: str \| null, generator: ModelInfo, alert_payload_sha256, generated_at}`; `alert_payload_sha256` ties the text to the audited alert (same rule as Q-AUD-05); the text never decides whether an alert exists | **Until answered:** the alert detail shows structured evidence only and "explanation unavailable" |

## Dashboard & evaluation

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-UI-01 | P1 | Are the filters (`since`, `until`, `region`, `limit≤500`, `offset`) enough? | as implemented | API scope |
| Q-UI-02 | P1 | Status vocabulary to display: `scored`, `insufficient_history`, `forecast_failed`, `detection_failed`, `audit` flag, `is_mock` | as implemented | operator trust |
| Q-UI-03 | P2 | Is the dashboard served from another origin (needs CORS)? | **Answered 2026-10-17 (ADR-009):** no CORS needed — the dashboard is server-side (Streamlit) and reaches the API through the SSH tunnel; the browser never calls the API | browser access |
| Q-UI-04 | P1 | **To P3 / P1.** How does the evaluation get the outputs of a 2019 run? The evaluation (layer 2 of ADR-008, on P4's branch `feat/be-012-cusum-detector`) reads the **run's artefacts**, not the live API after the fact (in-memory state, bounded capacity, ADR-003). Can `scripts/replay.py` get a `--responses-out <file.jsonl>` option that records every `WindowResult` (including errors) plus the client-side latency per request? | replay writes `responses.jsonl` (one line per window: response body + client latency); alerts read once at the end into `alerts.jsonl`; `/v1/status.started_at` recorded before and after — if it changed, the server restarted and the run is **invalid** | **Until answered:** recorded-artefact runs are not possible; evaluation only in process (EV-003, Tier 2) |
| Q-UI-07 | P1 | **To P3 + P4.** Residuals over time for the operator view (expected vs observed per sensor). Today residuals are only in the `POST /v1/windows` path and the audit payload. Complements Q-UI-05 on P4's branch (detector state / alert closing visibility) | `GET /v1/residuals?since&until&sensor&limit&offset` → `ResidualPage {items, total, limit, offset}` of `{window_end, status, detector_state, anomaly_score \| null, sensors: {<id>: {observed_m, expected_m, residual_m, z \| null, cusum \| null}}}`; additive (contract 1.1); retention = `SENTINEL_RESULT_CAPACITY` (same store as window results) | **Until answered:** no residual screen; the dashboard shows only alert evidence |

### Doc drift (to P3)

| ID | P | Question | Proposed default | Why / until answered |
|---|---|---|---|---|
| Q-DOC-01 | P1 | **To P3 (with P5).** Window `audit` values. On this branch contract §1 (`audit ∈ {recorded, pending, disabled}`) and the code (`AuditStatus` in `src/sentinel/schemas/api.py`) **agree**. The drift is on unmerged branches: `leo/local-version` (P5) and `zanetti/ingestion` have `AuditStatus = {recorded, disabled, failed, not_applicable}` while their contract §1 lists `{recorded, disabled, failed}` (no `not_applicable`); and P5's `docs/audit-and-narrative.md` records a **fail-open** decision (`audit="failed"`), which contradicts the fail-closed decision on `main` (Q-AUD-03, ADR-006) | keep `main` (`recorded`, `pending`, `disabled`, fail-closed); P5's branch is rebased on it before merge; any new value is added to code, contract §1 and examples together | the dashboard parses `WindowResult` with the contract models: an undeclared value is shown as "contract mismatch" |
| Q-DOC-02 | P2 | **To P3.** `docs/runbook-vm.md`: `docker inspect -f '{{.NetworkSettings.IPAddress}}' notebook` fails on the VM with `map has no entry for key "IPAddress"` (Docker with per-network settings). `172.17.0.2` still works | replace with `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' notebook` — **untested**, to be checked on the VM | the tunnel address can only be recovered by hand if the container is recreated |

## Platform

| ID | P | Question | Proposed default | Why |
|---|---|---|---|---|
| Q-PLAT-01 | P0 | When is the LinuxONE VM available, which Python, can we `uv sync` there? | Answered 2026-10-17: available, Python 3.12.3; uv 0.11.16 installed for `linux1` | ADR-001 risk |
| Q-PLAT-02 | P1 | May we `sudo apt install gcc python3-dev` on the shared VM? It lets `mypy` (via `librt`) and any wheel-less package build there; the model runtimes may need it too | install it (owner of the VM decides) | full `just check` on s390x |
