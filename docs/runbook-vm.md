# Runbook — shared API on the LinuxONE VM

One **integration** instance of the API runs from `~/shared/civicwater/main` (the `main`
branch, `pip install -e` into the container's `/opt/venv`) on port **8000**. Personal
instances use each member's own clone and port (`start.sh`, `API_PORT`, 8001–8006).

Everything is managed by [`scripts/shared_api.sh`](../scripts/shared_api.sh), run on the **VM
host** (`ssh linuxone`, user `linux1`), not in the Jupyter terminal: the host's `tmux` keeps
the API alive when SSH drops, and `docker exec` runs it inside the `notebook` container.

## Commands

```bash
cd ~/shared/civicwater/main
scripts/shared_api.sh start      # fails if something already listens on 8000
scripts/shared_api.sh status     # commit, history, registry, audit backlog, counters
scripts/shared_api.sh logs       # tail -f of the current log (Ctrl-C leaves the API running)
scripts/shared_api.sh restart    # stop + start
scripts/shared_api.sh stop
```

Defaults: official registry `config/sensors.battledim.json`, in-memory audit sink
(`SENTINEL_AUDIT_SINK=memory`, until the audit owner delivers the real sink), log level INFO,
no access log, one worker, BLAS threads = 1. Override with the variables listed at the top of
the script, e.g. `SENTINEL_LOG_LEVEL=WARNING scripts/shared_api.sh start`.

The forecaster and the detector are the mocks unless set (ADR-005). Real XGBoost (ML owner's
artefact):

```bash
SENTINEL_FORECASTER=xgboost \
SENTINEL_FORECASTER_PATH=/home/ibm-user/shared/civicwater/models/xgboost-33@0.1.0 \
scripts/shared_api.sh restart
```

Paths are the ones **inside the container**. Add `SENTINEL_FORECASTER_CONTRIBUTIONS=false`
for long replays (TreeSHAP dominates the latency, `docs/performance.md`) and
`SENTINEL_FORECASTER_TREE_FORMAT=compress_trees` to keep Snap ML on the CPU. If the model does
not load (missing artefact, `snapml` absent after the container was recreated), `start` fails
and prints the log: the API never falls back to the mock. `status` shows `forecaster` with
`is_mock: false`.

The script also forwards `SENTINEL_DETECTOR`, `SENTINEL_DETECTOR_CALIBRATION` and
`SENTINEL_DETECTOR_REGIONS` (only the variables that are set). The CUSUM detector refuses to start
with a calibration made for another forecaster ([README](../README.md#running-with-the-cusum-detector),
ADR-008); no calibration for `xgboost-33@0.1.0` exists yet (DEP-DET-05), so with XGBoost keep
the mock detector, which raises no alerts with millimetre residuals (`docs/performance.md`).

Logs: `~/shared/civicwater/logs/api-8000-<UTC start time>.log` (one JSON line per window,
≈ 26 MB for a full year at INFO; use `SENTINEL_LOG_LEVEL=WARNING` for long runs).

## Access from your machine

The container does not publish port 8000; tunnel to the container address:

```bash
ssh -N -L 8000:172.17.0.2:8000 linuxone
```

Then `http://localhost:8000/docs`. Nothing is exposed to the internet. If your SSH config
uses `ControlMaster`, add `-o ControlMaster=no -o ControlPath=none`: otherwise the forward
attaches to the shared master connection and survives Ctrl-C (close it with
`ssh -O cancel -L 8000:172.17.0.2:8000 linuxone`). If `172.17.0.2`
changes (container recreated), get it with
`docker inspect -f '{{.NetworkSettings.IPAddress}}' notebook` on the VM host.

## Updating the code

1. Announce it in the team channel: the update changes the default `import sentinel` of
   every notebook that does not use `start.sh`, and a restart wipes the API state.
2. `cd ~/shared/civicwater/main && git pull --ff-only` (never commit here).
3. If `pyproject.toml` dependencies changed: reinstall in the container
   (`docker exec -w /home/ibm-user/shared/civicwater/main notebook pip install -e .`).
4. `scripts/shared_api.sh restart`, then `status`.

## Restart semantics (state is in memory, ADR-003)

A stop/restart loses history, results, alerts, metrics, detector state and the audit pending
queue. After a restart:

- scoring needs 144 contiguous windows (12 h) again;
- re-send the replay **from the beginning of the period you want**; the first window after
  a restart may have any timestamp, then order is enforced (`out_of_order` 409 otherwise).

## Replaying 2019 into the shared API

The data owner generates the replay (`docs/data.md`):
`$SENTINEL_DATA_DIR/replay/v1/windows_2019.jsonl` (105 120 windows, official IDs, UTC).
Send it from the Jupyter terminal (same container, so `127.0.0.1` works):

```bash
nice -n 10 python scripts/replay.py "$SENTINEL_DATA_DIR/replay/v1/windows_2019.jsonl" --url http://127.0.0.1:8000
```

Measured 2026-10-17: 105 120 × `201` in 145 s (`docs/performance.md`). It loads the shared
VM: announce it and avoid running it during someone's latency measurement. Expected: every window `201` (or `200` if re-sent), 144
`insufficient_history`, the rest `scored`; mock alerts until the real models are wired.

## Troubleshooting

| Symptom | Check |
|---|---|
| `start` says something already listens | `status`; another member may have started it. Do not kill it without asking |
| `start` times out | the log tail printed by the script; usually a wrong registry path or a syntax error on `main` |
| every window 422 `sensor_set_mismatch` | the API runs with the provisional registry, or the windows use CSV column names (`Pressure_n1`) instead of IDs (`n1`) |
| 409 `out_of_order` for the whole replay | the API already holds later windows: `restart` and replay again |
| 503 `audit_backlog_full` | the audit sink is failing; `status` shows `audit_pending_events` (ADR-006) |
