# Demo runbook — operator dashboard (P6)

Owner: P6. Decisions: ADR-009 (dashboard), ADR-010 (layer-2 evaluation). Rule for the whole demo:
**never present mocks, a non-final calibration or a recording as live leak detection.** The
banners say it on screen; say it out loud too.

## 1. Before the demo (T − 30 min)

1. On the laptop (x86), in the repo: `uv sync --locked --group dashboard`.
2. Tunnel to the shared API on the VM (container port 8000). Pick a **free local port**: on the
   P6 laptop 8000 was already taken by another process, so 8800 is used here.

   ```bash
   ssh -f -N -o ControlMaster=no -o ControlPath=none -o ExitOnForwardFailure=yes \
       -L 8800:172.17.0.2:8000 linuxone   # ssh-config alias of the VM (host/user: team channel)
   curl -s localhost:8800/health          # {"status":"ok",...}
   ```
3. Check what the live API serves: `curl -s localhost:8800/v1/status` → note `last_timestamp`,
   `forecaster`, `detector`, `mocks_active`, `counters.alerts_stored`. Decide the path
   (section 3) from these, not from memory.
4. Open both dashboards in two browser tabs (these are `just dashboard` / `just
   dashboard-playback` plus an explicit `--server.port`, so both can run at once):

   ```bash
   # live (tab 1)
   SENTINEL_DASHBOARD_API_URL=http://127.0.0.1:8800 PYTHONPATH=. \
     uv run --group dashboard streamlit run dashboard/app.py --server.port 8501
   # recorded run (tab 2)
   SENTINEL_DASHBOARD_PLAYBACK_DIR=dashboard/playback/fixture-cusum PYTHONPATH=. \
     uv run --group dashboard streamlit run dashboard/app.py --server.port 8502
   ```
   If EV-003 produced a real 2019 run, point tab 2 at its directory instead of
   `fixture-cusum` (the banner then names that run).

## 2. Timed script (≈ 5 min)

| Time | Show | Say |
|---|---|---|
| 0:00 | Header of tab 1 | "CivicWater Sentinel watches 33 pressure sensors of L-Town, the BattLeDIM **simulated** network, in 5-minute windows, running on IBM LinuxONE." Point at *Simulated time*: "this is the replay's clock, not today's." |
| 0:45 | SIMULATED banner, Forecaster / Detector metrics | "Every model shown here is flagged: SIMULATED means a mock or a calibration that is not final. Nothing on this screen is leak detection yet." |
| 1:30 | Tab 1 list ("Recent alerts") | If empty **and** `/v1/status` shows `last_timestamp` 2019-12-31 23:55 and `counters.alerts_stored` 0 (step 3): "All of 2019 was replayed through this API with **mock** models: no alert was opened." An empty list can also mean a restart or alerts held by the audit; otherwise say only "no alert stored right now". Never present it as a result. |
| 2:00 | Tab 2, RECORDED REPLAY banner | "This is a recording of the real CUSUM detector code with an **invented** calibration and regions, a **mock** forecaster and a **synthetic** day (the 2019-03-14 date is only the replay clock), to show the evidence an operator gets." |
| 2:30 | Alert row → detail | Region, sensors with deviation and z, CUSUM statistic vs threshold, estimated onset and hours since onset as computed by the backend, audit reference raw, "explanation unavailable". |
| 3:30 | History: region filter `R-SYN-A`, page size | "History is paginated and filtered by time and region; the list refreshes every 5 s." |
| 4:00 | (optional) stop the tunnel | "If the API drops, the operator sees it": the header shows *API unavailable (…)* and the list *API unavailable since hh:mm … Data below may be stale*. |
| 4:30 | Evaluation slide / ADR-010 | "The 2019 evaluation is pre-registered: official BattLeDIM time window, all 23 leaks, false alarms per week; region accuracy and the euro score are not computed until we have regions and pipes." Show numbers **only** if EV-003 ran without the NOT A RESULT watermark. |
| 4:50 | Close | "Simulated benchmark; not validated on a real network." |

## 3. What each banner means

| Banner / state | Meaning | Say |
|---|---|---|
| SIMULATED (header) | `mocks_active`: a mock model or a non-final calibration (ADR-008 §B) | "not leak detection" |
| SIMULATED (alert) | this alert's `is_mock` | same |
| RECORDED REPLAY — run …, date | playback from files, not the live API | "recording, not live" |
| Provisional sensor IDs | registry is not the official one | "placeholder IDs" |
| API unavailable (…) (header) / API unavailable since hh:mm (list, wall clock) | the tunnel or the API is down; data may be stale | switch to tab 2 |
| Contract mismatch | the API answer does not match the contract models | do not show data; switch to tab 2 |
| explanation unavailable | no narrative endpoint yet (Q-AUD-07) | "the structured evidence is the source of truth" |

## 4. Contingency

| Failure on the day | Fallback | Screen / speech |
|---|---|---|
| VM or tunnel down | tab 2 (recorded run); optionally the API on the laptop (`just dev`, same commit) | RECORDED REPLAY banner; s390x numbers quoted from `docs/performance.md` with their date |
| Real forecaster (XGBoost/GRU) fails to load | the API keeps the mock; say so | `Forecaster` metric shows the mock, SIMULATED on |
| No real model at all | tab 2 | RECORDED REPLAY |
| LLM / narrative down | nothing changes (structured evidence only) | "explanation unavailable" |
| Audit down | fail-closed backend: an alert is published only after its audit record (ADR-006) | the alert does not appear until audited; `/v1/status` `audit_pending_alerts` shows how many wait ("audit unavailable" means the sink is disabled) |
| Local port 8800 busy | use another free port in the tunnel and in `SENTINEL_DASHBOARD_API_URL` | — |

## 5. Rehearsal log

Technical rehearsals by the agent are recorded in ticket UI-007
(`.scratch/p6-dashboard-evaluation/issues/07-demo-runbook-rehearsal.md`). The spoken rehearsal
with the presenter is still to be done by the team (twice, once with a forced fallback).
