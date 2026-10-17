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

All tests use small synthetic data (`sentinel.synthetic`, placeholder sensor IDs). No
BattLeDIM data is used or committed. Tests never sleep; concurrency tests use threads and
barriers.

## Layout

| File | Level | What it covers |
|---|---|---|
| `tests/test_schemas.py` | unit | window contract: valid payload, naive/offset timestamps, NaN/±Inf, strings/bools, unknown fields, bad IDs, schema version, content hash |
| `tests/test_ingestion.py` | unit | sensor registry (missing/unexpected), timestamp grid |
| `tests/test_history.py` | unit | order, repeated/older timestamps, gaps, capacity, past-only tail, restart, concurrent readers |
| `tests/test_sensors.py` | unit + API | registry from the data owner's `sensors.csv` (order, units, unknown types, duplicates, mixed units), provisional JSON, real-shaped window accepted by the API |
| `tests/test_timezone_policy.py` | unit + API | naive / offset timestamps, UTC assumption flag, `reject` policy, naive ≡ `Z` duplicate, setting validation |
| `tests/test_audit_fail_closed.py` | integration + API | alert withheld while audit is down, ordered flush on recovery, events queue behind pending ones, 503 when backlog full (state unchanged), disabled audit, status counters |
| `tests/test_config.py` | unit | env parsing, documented defaults, invalid config fails at startup, no silent non-mock models |
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
