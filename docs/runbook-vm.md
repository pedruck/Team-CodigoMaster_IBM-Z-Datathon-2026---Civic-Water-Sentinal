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
(`SENTINEL_AUDIT_SINK=memory`; `chain` for the hash-chained log, below), log level INFO,
no access log, one worker, BLAS threads = 1. Override with the variables listed at the top of
the script, e.g. `SENTINEL_LOG_LEVEL=WARNING scripts/shared_api.sh start`.

The forecaster and the detector are the mocks unless set (ADR-005). **Current shared API
(2026-10-17):** the real XGBoost below with `SENTINEL_FORECASTER_CONTRIBUTIONS=false` and
`SENTINEL_FORECASTER_TREE_FORMAT=compress_trees`, mock detector, 2019 replayed. Restart it with
the same variables to keep that configuration. Real XGBoost (ML owner's artefact):

```bash
SENTINEL_FORECASTER=xgboost \
SENTINEL_FORECASTER_PATH=/home/ibm-user/shared/civicwater/models/xgboost-33@0.1.0 \
scripts/shared_api.sh restart
```

Paths are the ones **inside the container**. `SENTINEL_FORECASTER_CONTRIBUTIONS` defaults to
`on_alert` (TreeSHAP only for the deviating sensors of an alert, so windows without an alert pay
nothing); `all` restores TreeSHAP on every window (~127 ms, `docs/performance.md`) and `false`
turns it off. Add
`SENTINEL_FORECASTER_TREE_FORMAT=compress_trees` to keep Snap ML on the CPU. If the model does
not load (missing artefact, `snapml` absent after the container was recreated), `start` fails
and prints the log: the API never falls back to the mock. `status` shows `forecaster` with
`is_mock: false`.

The script also forwards `SENTINEL_DETECTOR`, `SENTINEL_DETECTOR_CALIBRATION`,
`SENTINEL_DETECTOR_REGIONS` and `SENTINEL_DETECTOR_NETWORK` (only the variables that are set). The CUSUM detector refuses to start
with a calibration made for another forecaster ([README](../README.md#running-with-the-cusum-detector),
ADR-008): `config/calibration.xgboost-33.json` is for `xgboost-33@0.1.0`, so it needs
`SENTINEL_FORECASTER=xgboost` in the **same** command. The mock detector raises no alerts with
the millimetre residuals of XGBoost (`docs/performance.md`).

### Regions and pipe network for the CUSUM detector (ADR-011)

The regions are the repository's `config/regions.battledim.json` (the integration copy has it). The
pipe network is the data owner's file `~/shared/civicwater/data/network/v1/l-town.json` on the
host, `/home/ibm-user/shared/civicwater/data/network/v1/l-town.json` inside the container (sha256
`ff772dc1b68c59c05dc3a383fe739a624c054a7fd7c7544bc3b8c6bcbbf49b59`). With the calibration for the
forecaster served, `config/calibration.xgboost-33.json` (v1, sha256 `fad6ec0b…`, for `xgboost-33@0.1.0` with
neighbours; `docs/p4-calibration.md` §7), the shared API is started with (announce before restarting it):

```bash
SENTINEL_FORECASTER=xgboost \
SENTINEL_FORECASTER_PATH=/home/ibm-user/shared/civicwater/models/xgboost-33@0.1.0 \
SENTINEL_FORECASTER_TREE_FORMAT=compress_trees \
SENTINEL_DETECTOR=cusum \
SENTINEL_DETECTOR_CALIBRATION=config/calibration.xgboost-33.json \
SENTINEL_DETECTOR_REGIONS=config/regions.battledim.json \
SENTINEL_DETECTOR_NETWORK=/home/ibm-user/shared/civicwater/data/network/v1/l-town.json \
scripts/shared_api.sh restart
```

To check the files on the VM first, **without starting or restarting anything**, run this in the
Jupyter terminal (inside the container), in the integration copy:

```bash
cd ~/shared/civicwater/main
sha256sum ~/shared/civicwater/data/network/v1/l-town.json     # must be the hash above
SENTINEL_DATA_DIR=$HOME/shared/civicwater/data python -m sentinel.detection.offline.topology --check
SENTINEL_DATA_DIR=$HOME/shared/civicwater/data python -m pytest -q \
    tests/detection/test_network.py tests/detection/test_topology_build.py \
    tests/detection/test_topology_files.py tests/detection/test_candidate_pipe.py \
    tests/test_detector_network_startup.py
```

`--check` says whether `config/regions.battledim.json` is what the rule gives for that file. The
tests need nothing outside `/opt/venv` and read the network from `$SENTINEL_DATA_DIR`: with the
file in place none of them is skipped, `test_topology_files.py` pins its hash, and
`test_detector_network_startup.py` starts the server from environment variables only (official
registry, regions, `l-town.json`) and localises a synthetic ramp. If the file is missing they are
skipped, not failed, so check the summary line for `skipped`.

### Audit chain and explanations (BE-013)

Add `SENTINEL_AUDIT_SINK=chain SENTINEL_AUDIT_RUN=<run>` to the start command to record every
window in the audit owner's hash chain, `~/shared/civicwater/audit/<run>.jsonl` (inside the
container: `/home/ibm-user/shared/civicwater/audit/`), with the explanations of the alerts in
`<run>.explanations.jsonl`. Pick a new run name per replay (e.g. `replay-2019-<date>`): a
restart with the same name **continues** that chain (seq goes on) but the replay starts again
from January, so the log would hold the year twice. `SENTINEL_NARRATOR=llamacpp` loads Granite in
the API process (`LOCAL_LLM_*` are forwarded); `watsonx` is not forwarded on purpose, its key would
be visible in `ps`. `status` then prints the chain head; keep that `seq`/`hash` outside the VM and
verify with `python -m sentinel.audit.verify <file> --anchor-seq … --anchor-hash …`. Every
record is fsynced: time a short replay with the chain before a demo (`docs/audit-and-narrative.md`).

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

Measured 2026-10-17: 105 120 × `201` in 145 s with the mocks; 247 s with XGBoost (TreeSHAP off, `compress_trees`) (`docs/performance.md`). It loads the shared
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
