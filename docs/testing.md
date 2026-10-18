# Testing

## Commands

```bash
just check          # format -> lint -> typecheck -> test (all gates)
just ci             # same gates, format in --check mode (what CI runs)
just test           # pytest only
just test -k pipeline -x
```

Equivalent raw commands: `uv run ruff format src tests scripts`, `uv run ruff check src tests
scripts`, `uv run mypy` (strict, pydantic plugin), `uv run pytest`.

All tests use small synthetic data (`sentinel.synthetic`, placeholder sensor IDs; the dashboard
playback fixtures in `dashboard/playback/` use the official IDs over a synthetic signal). No
BattLeDIM data is committed; the only tests that read it are the ones skipped without
`SENTINEL_DATA_DIR` (`tests/test_p1_data.py`, one test of `test_offline_baseline.py`). Tests
never sleep; concurrency tests use threads and barriers.

## Layout

| File | Level | What it covers |
|---|---|---|
| `tests/test_schemas.py` | unit | window contract: valid payload, naive/offset timestamps, NaN/±Inf, strings/bools, unknown fields, bad IDs, schema version, content hash |
| `tests/test_ingestion.py` | unit | sensor registry (missing/unexpected), timestamp grid |
| `tests/test_history.py` | unit | order, repeated/older timestamps, gaps, capacity, past-only tail, restart, concurrent readers |
| `tests/test_sensors.py` | unit + API | official registry (`config/sensors.battledim.json`), provisional JSON, optional units, duplicates; API accepts a replay-shaped window with official IDs and rejects CSV column names |
| `tests/test_timezone_policy.py` | unit + API | naive / offset timestamps, UTC assumption flag, `reject` policy, naive ≡ `Z` duplicate, setting validation |
| `tests/test_audit_fail_closed.py` | integration + API | alert withheld while audit is down, ordered flush on recovery, events queue behind pending ones, 503 when backlog full (state unchanged), disabled audit, status counters |
| `tests/test_p1_data.py` | data (P1) | official registry, cleaning mask, data checks; skipped without `SENTINEL_DATA_DIR` |
| `tests/test_config.py` | unit | env parsing, documented defaults, invalid config fails at startup, no silent non-mock models; `SENTINEL_DETECTOR` (`mock` / `cusum`), `SENTINEL_DETECTOR_CALIBRATION` (required with `cusum`) and `SENTINEL_DETECTOR_REGIONS` (optional): both files refused with the mock detector, an empty path refused |
| `tests/test_detector_network_startup.py` | integration + API | `SENTINEL_DETECTOR_NETWORK`: a ramp alert names a pipe among the ramped sensors, no network gives `null`, refusals (network lacking a registry sensor, missing or invalid file naming the path); with the real `l-town.json`, the server started only from environment variables (official registry, committed regions, `network/v1/l-town.json`) localises a ramp in the zone under the tank | `Settings.from_env` → `create_app` |
| `tests/test_detector_startup.py` | integration + API (TestClient) | `SENTINEL_DETECTOR=cusum` through `Settings.from_env` → `create_app`: `/v1/status` reports `cusum` with `0.1.0+<8 hex of the file's sha256>` and `is_mock`, `mocks_active` stays true even with a non-mock forecaster, an injected detector wins; startup refused (`ConfigError` naming the file) for a calibration of another forecaster name or version (checked against the forecaster actually served), missing / extra / reordered sensors, missing, directory or invalid files, a region mapping that does not cover the registry or repeats a sensor; a synthetic ramp over HTTP gives `normal` then `alarm`, one `is_mock` alert with the ramped sensors, and the region of the mapping |
| `tests/test_metrics.py` | unit | nearest-rank percentiles, counters |
| `tests/test_pipeline.py` | integration (mocks, no HTTP) | insufficient history, past-only forecaster input, duplicate/conflict/out-of-order, gaps, capacity, forecaster/detector failures, contract violations, single stateful detector, audit events, audit failure, restart, concurrency |
| `tests/test_api.py` | API (TestClient) | every endpoint, status codes, error envelope, raw-JSON NaN, filters/pagination, status counters, concurrent requests, OpenAPI paths |
| `tests/test_integration.py` | end to end | one synthetic day (288 windows, documented defaults) through HTTP with mocks and in-memory audit |
| `tests/test_contract_examples.py` | contract | every file in `docs/examples/` validates (or is rejected with the documented code) |
| `tests/doubles.py` | — | failure doubles (flaky forecaster, partial forecaster, failing detector/audit) |

## Required scenarios → tests

| # | Scenario | Test(s) |
|---|---|---|
| 1 | valid payload | `test_valid_payload_is_accepted_and_ints_become_floats`, `test_valid_window_returns_201_with_result` |
| 2 | timestamp without timezone | `tests/test_timezone_policy.py` (read as UTC by default, 422 with `reject`), `test_naive_timestamp_is_rejected` (internal model), `test_unparseable_timestamp_returns_422_envelope` |
| 3 | non-finite values | `test_non_finite_values_are_rejected`, `test_nan_in_raw_json_is_rejected` |
| 4 | unknown fields | `test_unknown_top_level_field_is_rejected`, `test_unknown_field_is_rejected` |
| 5 | missing sensor | `test_missing_sensor_is_rejected_with_details`, `test_missing_sensor_returns_sensor_set_mismatch` |
| 6 | duplicates | `test_duplicate_is_idempotent_and_not_reprocessed`, `test_duplicate_returns_200_and_same_result`, `test_same_timestamp_with_different_values_is_a_conflict` |
| 7 | out-of-order window | `test_out_of_order_window_is_rejected_and_not_stored`, `test_out_of_order_and_conflict_return_409` |
| 8 | full history | `test_capacity_evicts_oldest`, `test_history_is_bounded_by_capacity` |
| 9 | insufficient history | `test_insufficient_history_until_required_windows`, `test_gap_resets_contiguity_and_requires_history_again` |
| 10 | forecaster failure | `test_forecast_failure_keeps_window_and_pipeline_continues`, `test_resending_failed_window_returns_stored_result_without_retry`, `test_unexpected_forecaster_exception_is_contained`, `test_forecast_violating_contract_is_rejected`, `test_forecast_failure_is_201_with_status` |
| 11 | detector failure | `test_detector_failure_is_reported_and_pipeline_continues` |
| 12 | alert returned by the API | `test_alert_is_exposed_by_list_and_detail`, `test_alert_filters_and_pagination` |
| 13 | integrated run with mocks | `test_synthetic_day_replay_with_injected_step` |
| 14 | concurrent requests | `test_concurrent_resend_of_same_window_creates_it_once`, `test_concurrent_distinct_windows_never_corrupt_order`, `test_concurrent_requests_are_serialised`, `test_concurrent_readers_and_writer_keep_invariants` |

## Detection tests (`tests/detection/`, CUSUM detector, BE-012a..f)

All calibrations, sensor IDs and region names in these tests are **invented** (placeholders
such as `P1`, `P01..P33`); they exercise mechanics and are not evidence of leak detection. The
detector is selected by environment variable in `tests/test_detector_startup.py` (above); here
it is injected into `build_pipeline` or called directly.

| File | Covers | Seam |
|---|---|---|
| `test_cusum.py` | statistic and cap, opening at `N` sensors, episodes (claim / release / no reset), states, onset, gaps and `reset_reason`, atomic rejection with fault injection, `is_mock` from provenance | `CusumDetector.evaluate` |
| `test_calibration.py` | artefact loading and validation, hash in `ModelInfo.version` and `calibration_id`, `calibrate()` (σ = max(floor, 1.4826·MAD), recovery of a known σ from AR(1) residuals), `for_pipeline` startup checks (`ConfigError`: sensors, order, forecaster) | `calibrate`, `write_artefact`, `for_pipeline` |
| `test_end_to_end.py` | synthetic days through `POST /v1/windows` → `GET /v1/alerts` with the detector injected into `build_pipeline`: one alert per step, concurrent alerts, release and re-alert, `suspect`, mismatched calibration fails detection not ingestion, gaps | HTTP API (the `SENTINEL_DETECTOR=cusum` selection is covered by `tests/test_detector_startup.py`) |
| `test_properties.py` | larger step detected no later, scale invariance, null-series run length vs Siegmund ARL0 (100 000 windows) | `CusumDetector.evaluate` |
| `test_determinism.py` | sha256 of a fixed replay (alerts, release, two gaps) as the reference for the x86 / s390x parity run (BE-015); regenerate with `uv run python -m tests.detection.test_determinism` only when behaviour changes on purpose. Digest on x86-64: `7c5ef97bf8ccee4e19695475142658b426be29f796298314ab054a277ad3eee3` (regenerated in BE-012i because alerts serialise `candidate_pipe`; without that key the replay gives the previous `d9106c15e4d5…`, checked, and that one passed on the VM on 2026-10-17); s390x for the new literal: **NOT_RUN** | `CusumDetector.evaluate` |
| `test_network.py` | `PipeNetwork` over a toy graph in the format of the data owner's `l-town.json`: distances along the pipes (`inf` between zones), zones, pipe-to-pipe distance between midpoints, `candidate_pipe` (one sensor, two equal sensors give the middle pipe, stronger evidence pulls the candidate, only the zone with the most evidence counts, ties between zones and between pipes go to the first in the file, refusal of empty / unknown / non-positive evidence), pumps and valves apart from the pipes, unknown keys ignored, refusal of another `schema_version`, a pipe without a positive length / not Open, an undeclared node, a repeated link or no pipe, and `network_id` | `PipeNetwork` |
| `test_topology_build.py` | `offline/topology.py` over a toy network: the region rule (complete linkage within a diameter, zones never mixed, numbering and order, tie-breaks, refusals), the command line writes a loadable regions file, identical bytes twice, never overwrites a different file, `--check` (accepts the file the rule gives, refuses a different or a missing one without writing), the network found through `$SENTINEL_DATA_DIR/network/v1/l-town.json` | `derive_regions`, `main` |
| `test_topology_files.py` | the committed regions file (sha256 pin, covers exactly the official pressure sensors) and, **when `$SENTINEL_DATA_DIR/network/v1/l-town.json` exists (as on the VM)**, the real network: sha256 pin (`ff772dc1…`), declared source model, every sensor is a node, no region crosses a valve or the pump, the pump and 3 PRVs are the only non-pipe links (905 pipes), the data owner's zones equal the groups of nodes the pipes join, and `topology --check` accepts the committed regions file (skipped without the file) | committed files, `l-town.json` |
| `test_candidate_pipe.py` | candidate pipe through `CusumDetector.evaluate` over a toy line network (ends, middle, sub-threshold sensors do not pull, `network_id`, network lacking a sensor refused) and through HTTP with the official registry and the L-Town regions, on a synthetic chain through the 33 sensors (everywhere) and on the real `l-town.json` (when it exists): `Alert` 1.1, 1.0 still accepted, the candidate of `n1`/`n4`/`n31` is in their zone | `evaluate`, HTTP API |
| `test_regions.py` | `region`, `region_scores`, `regions_id`, tie-break, mapping validation, startup failure when the mapping does not cover the registry, `GET /v1/alerts?region=` | `evaluate`, HTTP API |
| `test_offline_baseline.py` | `offline/baseline.py` (BE-012f, needs pandas): recovery of a known weekly profile, the expected value of a month never uses that month (median and mean), `slot_minutes` must divide a day, a slot with no training data is refused, input refused if not a finite, timezone-naive, single-year table, `write_oof` byte-for-byte reproducible (gzip without timestamp or file name, floats that round-trip) with the two hashes feeding `calibrate()`, folds file as time blocks, `load_pressures_2018` refuses a file that differs from `MANIFEST.json` before parsing it; one real-data test (shape and finiteness only, no values), skipped without `SENTINEL_DATA_DIR` | `oof_hour_of_week_residuals`, `write_oof`, `load_pressures_2018` (DataFrames; fake `v1` directories in `tmp_path`) |
| `test_offline_devcal.py` | `offline/devcal.py` (BE-012f, needs pandas): residual structure statistics against hand-worked cases (lag-1 autocorrelation, diurnal scale with the σ floor, pairwise and common-mode correlation, extreme rate), σ sensitivity to the leak mask, sweep summary, `run` writes every output, artefact loads in the detector, hashes are those of the files, byte-for-byte reproducibility, no numeric default, no 2019 file opened, refusal of inconsistent parameters or labels that differ from the manifest, CLI; real-2018 test skipped without `SENTINEL_DATA_DIR` | `devcal.run`, `calibrate`, `sweep` |
| `test_offline_sweep.py` | `offline/sweep.py`: `replay` against expectations worked out by hand from ADR-007 (multiples of 0.5, exact in float64) and **bit-for-bit parity with `CusumDetector`** over seeded scenarios (several sensors per alert, release then re-claim, unknown onset, ties at `h_close` and at the cap); input and parameter refusals (irregular grid, non-finite or non-float64 `z`, parameters the artefact would refuse); `sweep` table shape, order, determinism, counts and medians per leak type; false alarms are monotone in `h` for one sensor without cap, and a **counterexample with the cap active** checked against the real detector (ADR-008 §E) | `replay`, `sweep` vs `CusumDetector.evaluate` |
| `test_offline_layer1.py` | `offline/layer1.py`: delay from `start` and from `peak` (negative if before the peak), matching window `[start, min(end, start + max_delay)]` per leak type, every opening is exactly a detection, a repeat or a false alarm, false alarms per week, misses listed per type, refusals (unknown type, repair before start, unparsed dates, naive vs aware times, no parameter with a default). The matching rule is **provisional** (Q-UI-06): the tests use invented rules | `evaluate`, `MatchRule` |
| `artefacts.py`, `residuals.py`, `networks.py` | helpers: invented artefacts; synthetic residual generator (AR(1), diurnal heteroscedasticity, common mode); toy pipe-network files | — |

## End-to-end against the real server (manual, synthetic data)

`TestClient` runs in process, so the startup wiring was also checked once against a real
`uvicorn` process and real HTTP. Run 2026-10-17 on the tree of `79f2647` (identical to the
pre-squash tip `6fba793` that was tested), x86-64, single worker, `127.0.0.1`. Everything was
synthetic (invented sine plus noise, invented calibration, placeholder IDs; one run with the
official IDs and synthetic values): it checks the **mechanics**, not leak detection (AGENTS §7).
The helper scripts were outside the repository (not committed); the procedure is below.

**Result: no bug found.** 84 automated checks over the raw responses, plus an independent
re-implementation of the CUSUM formula (importing nothing from `sentinel.detection`) that
predicted `detector_state` for every window and every alert; the server matched it window by
window in all five replays.

| # | Scenario | Result |
|---|---|---|
| 1 | calibration from `calibrate()` + `write_artefact()` served with regions; ramp on 3 sensors (= N) of one region | 330 windows: 144 `insufficient_history`, then `normal`, `suspect` (3), `alarm` (17); exactly 1 alert, `is_mock: true`, `region` = the ramp region, `deviating_sensors` = the ramped sensors, `cusum.statistic` > `threshold`; `?region=` returns it only under its region; resend of the alarm window is `200` with `duplicate: true` |
| 1b | ramp on 4 sensors | the alert lists only the sensors above `h` at the opening window (3 of 4); the 4th stays unclaimed (alone it cannot open an alert) |
| 2 | step of 0.5 m, persistence forecaster | no alert, `normal` throughout (`S` peaks below `h` and decays) |
| 2b | step of 3.0 m | an alert **does** open at the step window (`S` jumps to the cap `c·h`): a step does not accumulate, but one with `z > h + k` in a single window crosses `h` |
| 3 | startup refusals (5 required + 6 extra cases) | exit code 1 before listening, port free afterwards: calibration of another forecaster (name, or version), reordered or missing sensors, `cusum` without the calibration variable, missing path, regions that miss a sensor or repeat one, calibration not JSON, calibration set with the mock detector |
| 4 | official registry `config/sensors.battledim.json`, synthetic readings and calibration | same behaviour with the IDs `n1`…`n769`; a window with the placeholder IDs gets `422 sensor_set_mismatch` |
| 5 | no `SENTINEL_*` variable | `/v1/status` reports `mock-threshold` and `mock-persistence`; the README flow (`scripts/make_synthetic.py` + `scripts/replay.py`, 288 windows) raised the mock alert |

Observations that are not bugs of the detector: (a) with the persistence forecaster the ramp
alerted 13 windows (65 min) after it started although it alone would cross `h` in about 9,
because persistence cannot follow the synthetic diurnal sine, which was rising at that hour;
(b) `/v1/status` shows `history.size` 289 for `capacity` 288, because the pipeline keeps the
past plus the window being scored (`Pipeline.__init__`), and `throughput_windows_per_s` is
uptime-based (idle time included), so wall time of the client was used instead; (c) a refused
startup prints a Python traceback whose last line is `sentinel.config.ConfigError: ...` (the same
as the older `SENTINEL_FORECASTER=gru`); for failures that come from pydantic (calibration not
JSON, a sensor in two regions) the message spans three lines and the last line of the traceback
is a pydantic documentation URL; (d) the liveness endpoint is `GET /health`.

Not exercised there (covered by unit tests only): gaps, restart in the middle of an episode,
concurrency. State is in memory (ADR-003), so every scenario started from a fresh process.

**Reproduce** (replace `$T` by the worktree, `$OUT` by a scratch directory outside the repo):

```bash
cd $T && uv sync --locked
# 1. Files. Python, using only the public API: residuals of the registry's pressure sensors
#    (e.g. persistence residuals of a `sentinel.synthetic` series) -> sentinel.detection.calibration
#    .calibrate(provenance="synthetic", forecaster=ForecasterIdentity("mock-persistence", "0.1.0"),
#    ...) -> write_artefact(artefact, "$OUT/calibration.json"); a regions JSON that splits the
#    registry's pressure sensors into 3 groups; a JSONL of 330 windows 5 minutes apart built with
#    sentinel.synthetic.window_payload(ts, registry, drop_m=..., drop_sensors=...), flat for 300
#    windows and then a ramp (drop_m grows by a fixed amount per window) on exactly N sensors.

# 2. Real server (clean environment, one worker)
env -i PATH=$PATH HOME=$HOME \
  SENTINEL_DETECTOR=cusum \
  SENTINEL_DETECTOR_CALIBRATION=$OUT/calibration.json \
  SENTINEL_DETECTOR_REGIONS=$OUT/regions.json \
  .venv/bin/uvicorn sentinel.main:create_app --factory --host 127.0.0.1 --port 8765 &
curl -s localhost:8765/health

# 3. Replay over HTTP (the report used a small stdlib client that kept every response body;
#    scripts/replay.py sends the same windows but keeps only latency)
uv run python scripts/replay.py $OUT/ramp.jsonl --url http://127.0.0.1:8765

# 4. Queries
curl -s localhost:8765/v1/status                      # detector cusum 0.1.0+<8 hex>, is_mock true
curl -s localhost:8765/v1/alerts
curl -s 'localhost:8765/v1/alerts?region=R-centre'    # also another region and an unknown one
curl -s localhost:8765/v1/alerts/<alert_id>

# 5. Stop and check the port is free
kill <pid>; ss -ltnp | grep 8765

# Refusals: the same command without `&`, with one defect per run (wrong forecaster in the
# artefact, reordered sensors, no SENTINEL_DETECTOR_CALIBRATION, a path that does not exist,
# a regions file without one sensor); the process must exit with code 1 and never listen.
# Official registry: add SENTINEL_SENSOR_REGISTRY=config/sensors.battledim.json and files that
# use its IDs. Default detector: start with no SENTINEL_* variable.
```

Timings of that run are in `docs/performance.md`.

## Running on the LinuxONE VM (s390x)

`mypy` cannot be installed there yet: its dependency `librt` 0.16.0 has no s390x wheel on
PyPI and the VM has no C compiler (`gcc`), so `uv sync --locked` (which includes the dev
group) fails while building it. The runtime dependencies install from wheels. Until Q-PLAT-02
is answered, run the gates that work, with the versions pinned in `uv.lock`:

```bash
uv sync --locked --no-dev
uv run --no-sync --with pytest==9.1.1 --with httpx2==2.13.1 --with ruff==0.17.1 pytest
```

Type checking is platform-independent and runs in CI on x86.

## Not covered (yet)

- Line/branch coverage is **not measured** (no coverage tool added; NOT_RUN).
- Real models, real sensor IDs, real data: blocked (`docs/dependencies.md`).
- `mypy` on s390x (see above); no CI runner on s390x.
- Load test at saturation: BE-017.
- CI: `.github/workflows/ci.yml` runs `just ci` (format check, lint, mypy, pytest) on every PR and push to `main`, Python 3.12 on `ubuntu-latest` (x86_64 only).

## Last recorded run

See `docs/status.md` → "Verification" for the date, commands and counts of the latest run.
