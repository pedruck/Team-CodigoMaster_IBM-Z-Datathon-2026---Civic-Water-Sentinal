# Configuration and running modes

Reference for operators and developers: environment variables, the real forecaster, the CUSUM
detector and the audit chain. These sections used to be the middle of the README; they were moved
here unchanged (only a relative link was adjusted) so the [README](../README.md) can be a landing
page. For the quick start and the API table, see the README; for the shared API on the LinuxONE VM,
[`runbook-vm.md`](runbook-vm.md).

## Configuration

Environment variables `SENTINEL_*`, all with defaults — see [`.env.example`](../.env.example).
Main ones: `SENTINEL_HISTORY_CAPACITY` (288), `SENTINEL_MIN_HISTORY_WINDOWS` (144),
`SENTINEL_NAIVE_TIMESTAMP_TZ` (`UTC`: timestamps without timezone are read as UTC; `reject`),
`SENTINEL_SENSOR_REGISTRY` (placeholder JSON by default; `config/sensors.battledim.json`, the
official registry, for real data), `SENTINEL_AUDIT_SINK` (`null` | `memory` | `chain`, below), `SENTINEL_DETECTOR`
(`mock` | `cusum`, below). Invalid values fail at startup. State is **in memory** and lost on
restart (ADR-003).

Write path safety (contracts §1): `SENTINEL_INGEST_TOKEN` or `SENTINEL_INGEST_TOKEN_FILE` makes
every `POST` require `Authorization: Bearer <token>` (reads stay open; without a token the API
logs a warning); `SENTINEL_MAX_BODY_BYTES` (64 KiB); `SENTINEL_MAX_GAP_SECONDS` (1 day: a window
further ahead of the last one is refused, `0` = off); `SENTINEL_FUTURE_TOLERANCE_SECONDS` (1 h
past the server clock). Readings outside the registry's `limits` get 422. `scripts/replay.py`
needs `--url` and takes `--token-file` (or `SENTINEL_INGEST_TOKEN`).

## Running with a real forecaster

| Variable | Meaning |
|---|---|
| `SENTINEL_FORECASTER` | `mock` (default), `baseline`, `xgboost` or `gru` |
| `SENTINEL_FORECASTER_PATH` | artefact of the ML owner (`docs/p2-forecasting.md`). **Required** unless `mock` |
| `SENTINEL_FORECASTER_CONTRIBUTIONS` | XGBoost TreeSHAP: `on_alert` (default; only the deviating sensors of an alert), `all` or `off` |
| `SENTINEL_FORECASTER_SHA256` | optional pin: the artefact's SHA-256 (`sentinel.forecasting.integrity`); any other artefact stops startup. The served version always carries the first 8 digits (`0.1.0+808746bc`) |
| `SENTINEL_FORECASTER_ENGINE` | `snapml` (default) or `xgboost` |
| `SENTINEL_FORECASTER_TREE_FORMAT` | Snap ML: `auto` (default; NNPA on LinuxONE), `compress_trees` or `zdnn_tensors` |

The model is loaded once at startup; if it cannot be loaded the server does not start (it never
falls back to the mock). XGBoost needs numpy, xgboost and snapml, which come from the LinuxONE
image (`/opt/venv`), not from `uv sync`. On the VM: `docs/runbook-vm.md`. A CUSUM calibration
names the forecaster it was made for and must match the one served.

## Running with the CUSUM detector

The default detector is the `mock` one (`mock-threshold`, `is_mock=true`; not CUSUM). To serve
the CUSUM detector (ADR-007, ADR-008) set:

| Variable | Meaning |
|---|---|
| `SENTINEL_DETECTOR` | `mock` (default) or `cusum` |
| `SENTINEL_DETECTOR_CALIBRATION` | path to the calibration artefact (JSON). **Required** with `cusum`; no default path, no number of the detector has a default in code |
| `SENTINEL_DETECTOR_REGIONS` | optional path to a sensor → region mapping (JSON); only with `cusum` |
| `SENTINEL_DETECTOR_NETWORK` | optional path to the pipe network (JSON, ADR-011): each alert then names a `candidate_pipe`; only with `cusum` |

```bash
SENTINEL_DETECTOR=cusum \
SENTINEL_DETECTOR_CALIBRATION=run/calibration.json \
SENTINEL_DETECTOR_REGIONS=run/regions.json \
just dev
```

The files must match the registry in use (`SENTINEL_SENSOR_REGISTRY`) and the forecaster served
(today `mock-persistence 0.1.0`). Minimal calibration artefact (abridged; every field is
required, unknown fields are refused, and the numbers below are **invented**, only there to show
the shape):

```text
{
  "provenance": "synthetic",
  "forecaster": {"name": "mock-persistence", "version": "0.1.0"},
  "residuals_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "folds_sha256": "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
  "sigma_m": {"P01": 0.0743, "P02": 0.0746, ... one entry per pressure sensor of the registry, in registry order},
  "k": 0.5, "h": 10.0, "n_min": 3, "c": 2.0, "h_close": 5.0
}
```

`provenance` is `synthetic`, `dev-baseline-2018` or `battledim-2018-oof`; `sigma_m` is the
residual sigma in metres; `n_min` is N; `c > 1` gives the cap `c·h`; `h_close <= h`. The file is
written by `calibrate()` + `write_artefact()` (`sentinel.detection.calibration`). Optional region
mapping: every pressure sensor of the registry in exactly one region, and no other; the order of
the regions in the file breaks ties:

```text
{"regions": {"R-north": ["P01", "P02", ...], "R-south": ["P12", "P13", ...]}}
```

Without it `region` and `region_scores` stay null; without the network `candidate_pipe` stays null.
For the L-Town data use `SENTINEL_SENSOR_REGISTRY=config/sensors.battledim.json`,
`SENTINEL_DETECTOR_REGIONS=config/regions.battledim.json` and
`SENTINEL_DETECTOR_NETWORK=$SENTINEL_DATA_DIR/network/v1/l-town.json` (ADR-011): the pipe network
is the data owner's file, kept with the other data outside git (`docs/data.md`). The regions file is
the one in `config/`; `python -m sentinel.detection.offline.topology --check` verifies that it is
what the rule gives for the network file you have (on the VM too), and never overwrites a different
file. `GET /v1/status` then reports
`detector: {"name": "cusum", "version": "0.1.0+<first 8 hex of the artefact's sha256>",
"is_mock": true}`.

**The server refuses to start** (`ConfigError`, before it listens) when: `cusum` is selected
without `SENTINEL_DETECTOR_CALIBRATION`; either path is set while `SENTINEL_DETECTOR` is `mock`
(it would be silently ignored) or is empty; a file is missing, unreadable or invalid (the message
names it); the calibration was made for another forecaster name or version than the one served;
its sensors differ from the registry's pressure sensors or are in another order; the region
mapping does not cover exactly the registry's pressure sensors or puts a sensor in two regions;
the pipe network does not contain every pressure sensor of the registry as a node.

**What to expect.** With the mock persistence forecaster (expected = previous reading) a *step*
in the readings is a residual in one window only, so `S` does not accumulate; a *ramp* (pressure
keeps falling by about the same amount each window) leaves a constant residual and `S`
accumulates (ADR-007 §1). A step larger than `(h + k)·σ` on `N` sensors in a single window still
opens an alert, because `S` jumps in that one window. Only an artefact with `provenance =
battledim-2018-oof` gives `is_mock=false`; any other provenance makes the detector, its alerts
and `mocks_active` mock. `config/calibration.xgboost-33.json` is the one for the served
`xgboost-33@0.1.0` (BE-012h v1, ADR-008 §G). Mechanics checked end to end
with synthetic data: `docs/testing.md`.

## Running with the audit chain

`SENTINEL_AUDIT_SINK=chain` uses the audit owner's hash-chained log (`docs/audit-and-narrative.md`,
contracts §4): one JSON line per event in `<SENTINEL_AUDIT_DIR>/<SENTINEL_AUDIT_RUN>.jsonl`
(default directory `~/shared/civicwater/audit`, outside every clone), fsynced before the window
is answered, verified at startup and extended across restarts. A broken file stops the API.
Every recorded alert also gets a stored explanation in `<run>.explanations.jsonl`, written in
the background by `SENTINEL_NARRATOR` (`none` = deterministic text; `llamacpp`, `local`,
`watsonx` = Granite, configured by `LOCAL_LLM_*` / `WATSONX_*`).

```bash
SENTINEL_AUDIT_SINK=chain SENTINEL_AUDIT_RUN=local-test SENTINEL_AUDIT_DIR=data/audit just dev
python -m sentinel.audit.verify data/audit/local-test.jsonl   # anyone can check the chain
```

Use one run name per process: two APIs writing the same file make the second append fail
(fail-closed, 503 after the backlog fills).
