# Integration contracts (v1.0 — Proposed)

Code is the source of truth: `src/sentinel/schemas/` (Pydantic) and the ports in
`src/sentinel/forecasting/interface.py`, `src/sentinel/detection/interface.py`,
`src/sentinel/audit/sinks.py`. HTTP schemas: `GET /openapi.json` or `/docs` on a running
server. Examples in [`docs/examples/`](examples/) are validated by
`tests/test_contract_examples.py`.

All contracts are `Proposed` until the owner listed in each section confirms them
(`docs/questions-for-team.md`). Changes follow [§6](#6-versioning-and-change-process).

Common rules for every contract model:
- unknown fields are rejected (`extra="forbid"`), models are immutable;
- timestamps are normalised to UTC (serialised with `Z`). Internal models (`SensorWindow`, what
  forecasters and detectors receive) always carry a timezone. Only the HTTP input
  (`SensorWindowIn`) may omit it — see §1;
- numbers must be finite JSON numbers: `NaN`, `±Infinity`, strings (`"50.1"`) and booleans
  are rejected;
- sensor IDs: 1–64 chars, `[A-Za-z0-9_.:-]`.

## 1. Window — `POST /v1/windows` (owner to confirm: data/replay)

| Field | Type | Rule |
|---|---|---|
| `schema_version` | `"1.0"` | required; other values rejected |
| `timestamp` | ISO datetime, with or without TZ (`2019-03-14T12:30:00Z`, `2019-03-14 12:30:00`) | **end** of the 5-min aggregation interval; must be on the 300 s UTC grid (`SENTINEL_REQUIRE_ALIGNED_TIMESTAMPS`). Without TZ it is read as UTC and the result says `timestamp_tz_assumed: true`; `SENTINEL_NAIVE_TIMESTAMP_TZ=reject` turns this into 422 `timestamp_without_timezone` |
| `pressures` | `{sensor_id: number}` | non-empty; metres (data/v1) |
| `flows` | `{sensor_id: number}` | m³/h (data/v1) |
| `tank_levels` | `{sensor_id: number}` | metres (data/v1) |
| `source` | string \| null | optional replay/run ID; not part of the content hash |

Sensor set: every window must contain **exactly** the IDs of the sensor registry
(`SENTINEL_SENSOR_REGISTRY`), no missing and no extra sensor, no `null` values. For real
data, use the data owner's official registry `config/sensors.battledim.json` (BattLeDIM
network-element IDs: `n1`…`n769`, `p227`, `p235`, `PUMP_1`, `T1`; registry order = array
order). The SCADA CSV column names (`Pressure_n1`, `Flow_p227`, `Level_T1`) are **not** sensor
IDs and are rejected (`docs/data.md`). The default `config/sensors.provisional.json` keeps **placeholder IDs**
(`P01..P33`, `F01..F03`, `T01`) for development and tests.

Labels (leak start/pipe) must never be sent in a window: they are for offline evaluation only
(an unknown field such as `leak_label` is rejected anyway).

Time-series rules (ADR-004): duplicate → 200 idempotent; same timestamp with different data
→ 409 `window_conflict`; older than last accepted → 409 `out_of_order`; gap → accepted with
`gap_windows`, contiguity resets; fewer contiguous past windows than required → accepted,
`insufficient_history`. A resend is recognised as a duplicate only while its result is
retained (`SENTINEL_RESULT_CAPACITY`, default 2016 windows = 7 days); older resends get
409 `out_of_order`.

Valid: [`window.valid.json`](examples/window.valid.json) (abridged):

```json
{
  "schema_version": "1.0",
  "timestamp": "2019-03-14T12:30:00Z",
  "pressures": {"P01": 44.8754, "P02": 44.9302, "…": "P03..P32 omitted here", "P33": 49.8362},
  "flows": {"F01": 19.9344, "F02": 19.8424, "F03": 19.847},
  "tank_levels": {"T01": 2.8739},
  "source": "replay-2019-example"
}
```

Invalid examples (all 422):

| File | Why | `error.code` |
|---|---|---|
| `window.invalid.string-value.json` | `"P01": "50.1"` | `validation_error` |
| `window.invalid.unknown-field.json` | extra `leak_label` | `validation_error` (`extra_forbidden`) |
| `window.invalid.missing-sensor.json` | `P33` absent | `sensor_set_mismatch` (`details.missing_pressures`) |
| `window.invalid.misaligned.json` | `00:02:30Z` | `timestamp_misaligned` |

Accepted with assumption: [`window.naive-timestamp.json`](examples/window.naive-timestamp.json)
(`"2019-03-14T00:00:00"`) → 201 with `timestamp_tz_assumed: true` by default; 422
`timestamp_without_timezone` with `SENTINEL_NAIVE_TIMESTAMP_TZ=reject`.

Response: `WindowResult` — [`window-result.scored.json`](examples/window-result.scored.json).
`status ∈ {scored, insufficient_history, forecast_failed, detection_failed}`,
`audit ∈ {recorded, pending, disabled}` (fail-closed: `pending` = events not yet in the log,
alerts withheld — see §4), `issues[]`, `latency_ms` per stage. The result is a snapshot taken
when the window was processed: an idempotent resend returns it unchanged (e.g. still
`pending` after the queue was flushed); current audit state is in `/v1/status` and
`GET /v1/alerts/{id}`.

## 2. Forecaster (owner to confirm: ML)

```python
class Forecaster(Protocol):
    info: ModelInfo                 # name, version, is_mock
    min_history_windows: int        # e.g. 144 for 12 h
    def predict(self, request: ForecastRequest) -> ForecastResult: ...
```

`ForecastRequest` — guarantees given by the backend:
- `target_timestamp`: timestamp of the window being scored.
- `history`: tuple of `SensorWindow`, **ascending, contiguous, strictly before** the target;
  last element = target − 300 s; length ≥ `max(SENTINEL_MIN_HISTORY_WINDOWS,
  min_history_windows)` and ≤ `SENTINEL_HISTORY_CAPACITY` (default 288).
- `current`: the window being scored (flows, tank level, other sensors, calendar from
  `timestamp`). **No-leakage rule:** to predict sensor `s` the model must not read
  `current.pressures[s]`. Whether it may read the *other* sensors' current pressures is a
  modelling choice the design document makes for XGBoost (§3, Table 2) — declare it (Q-ML-02).
- `pressure_sensor_ids`: sensors to predict, in registry order — use it as the array order
  if the model works with arrays (ONNX fixed shapes).

`ForecastResult` — [`forecast-result.valid.json`](examples/forecast-result.valid.json):
- `timestamp` must equal `target_timestamp`;
- `expected_pressures` must contain **exactly** `pressure_sensor_ids`, same unit as input;
- `model`: `ModelInfo`; `contributions` (optional): `{sensor: {feature: metres}}`
  (TreeSHAP for XGBoost, design doc §6).

Errors: raise `ForecastError` for expected failures (model not loaded, bad shape). Any
exception → window `forecast_failed` (not retried, ADR-004). A result violating the rules
above → `forecast_failed` with `issues[0].code = "invalid_forecast"`. Insufficient history
never reaches the forecaster. Observed and expected values are never mixed: the forecaster
only returns expected values; residuals are the detector's job.

Mock: `PersistenceMockForecaster` (expected = previous window). `is_mock=true`. It is not a
model and its output is not evidence of anything.

## 3. Detector (owner to confirm: detection/localisation)

```python
class Detector(Protocol):
    info: ModelInfo
    def evaluate(self, observed: Mapping[str, float],      # current pressures, registry sensors
                 expected: Mapping[str, float],            # ForecastResult.expected_pressures
                 timestamp: datetime) -> DetectionResult: ...
```

- **Stateful:** CUSUM accumulates across windows. The backend creates **one instance per
  process** and calls it strictly in timestamp order, never concurrently, never twice for
  the same window. After a restart the state starts empty (ADR-003).
- **Atomic:** either update state and return, or raise (`DetectionError`) without having
  changed state. This holds for any exception, not only `DetectionError` (the pipeline
  catches `Exception`).
- Gaps: the detector is not told explicitly; it receives non-consecutive timestamps. Each
  missing window counts as zero residual: every S decays by `k` per missing window
  (ADR-007).

`DetectionResult` — [`detection-result.valid.json`](examples/detection-result.valid.json):

| Field | Meaning |
|---|---|
| `timestamp` | must equal the evaluated window (else `invalid_detection`) |
| `model` | `ModelInfo` |
| `state` | `normal` \| `suspect` \| `alarm` |
| `anomaly_score` | optional scalar; with the CUSUM detector, the N-th largest S among unclaimed sensors, dimensionless (σ units) |
| `residuals` | `{sensor: {residual_m, z?, cusum?}}` |
| `alerts[]` | alerts **opened at this window** (not ongoing ones): `region?`, `suspected_onset?`, `cusum{statistic, threshold}?`, `deviating_sensors[] {sensor, deviation_m, z?, since?}` (≥1), `summary?` |
| `region_scores` | optional `{region: score}` |
| `diagnostics` | free `{key: number|string|bool}` (e.g. `k`, `h`) |
| `complete`, `issues[]` | `complete=false` → result kept, issue `incomplete_detection` |

**Sign convention (decided, Q-DET-02, `docs/team-workflow.md`):**
`residual_m` / `deviation_m` = observed − expected (negative when pressure is low) and
`z = (expected − observed)/σ` (**positive** when pressure is low, as CUSUM accumulates it,
design doc §3). So in a leak `deviation_m < 0` and `z > 0`. The design doc §6 example
(`deviation_m −0.9`, `z −4.1`) has the wrong z sign. Enforced in the mock and by
`test_pressure_drop_has_negative_deviation_and_positive_z`.

**CUSUM semantics (`CusumDetector`; ADR-007, calibration in ADR-008, both `Proposed`):**
- Per sensor `S ← min(c·h, max(0, S + z − k))` (`z` as above, positive on a drop);
  `residuals[s].cusum` is `S`, so it is never negative and never above the cap `c·h`. σ, `k`,
  `h`, `N`, `c`, `h_close` come from the calibration artefact, never from code defaults.
- An alert opens when ≥ `N` **unclaimed** sensors have `S > h`; they become *claimed* and do
  not count again until `S < h_close`. No reset after an alert; no closing event.
- `cusum.statistic` = N-th largest `S` among unclaimed sensors, `threshold` = `h`; an alert
  opens iff `statistic > threshold`. `anomaly_score` carries the same value on every window
  (null while fewer than `N` sensors are unclaimed).
- `state`: `alarm` if any sensor is claimed; else `suspect` if an unclaimed sensor has
  `S > h` (fewer than `N`); else `normal`.
- `deviating_sensors` = the unclaimed sensors with `S > h` that open the alert; `since` = last
  window with `S = 0`, so a new episode that starts before `S` returned to 0 inherits the old
  onset (Q-DET-06); `suspected_onset` = earliest `since`, or `null` when any deviating sensor
  never had `S = 0` since the first window the detector saw.
- Gaps: with `m` missing windows every `S` decays to `max(0, S − k·m)` before the current
  window is processed. The onset and the release happen inside the gap: the last window of
  the gap counts as a zero whenever the decay left `S` at 0, and a claimed sensor is
  released if `min(decayed S, new S) < h_close`.
- `region` / `region_scores` need a sensor → region file `{"regions": {"<region>": ["<sensor>",
  ...]}}` (file order is the tie-break; each pressure sensor in exactly one region; startup
  fails if it does not cover exactly `registry.pressure`). `region_scores` is emitted on
  every window with every region (`0.0` when empty): the sum of `S` over the unclaimed
  sensors with `S > h`, the same set as `anomaly_score` (after the `h_close` release, before
  the sensors of the alert opening in this window are claimed). `region` = region with the
  largest sum (tie: first in the file). Without the file both are null and no new key appears.
- `diagnostics`: `k`, `h`, `n_min`, `c`, `cap`, `h_close`, `provenance`, `calibration_id`
  (sha256 of the artefact file), `skipped_windows`; `reset_reason="gap"` only when a skip
  emptied every `S`; `regions_id` (sha256 of the mapping file) only with a mapping.
- `complete` is always `true`. `DetectionError` is raised, with the state unchanged
  (atomic), when the timestamp is ≤ the last evaluated one or has no timezone, when the
  observed or expected sensor set differs from the calibration's, or when a value or `z` is
  not finite.
- `ModelInfo`: `name="cusum"`, `version="0.1.0+<8 hex of the artefact hash>"`,
  `is_mock=true` unless the calibration `provenance` is `battledim-2018-oof`.
- `state` is returned by `POST /v1/windows` (`detector_state`) and is in the `window.*`
  audit payload together with `anomaly_score`; no GET endpoint exposes either (Q-UI-05).
Mock: `ThresholdMockDetector` — opens one alert when ≥ N sensors are ≥ 3σ below expected.
Not CUSUM, no region.

## 4. Alert and audit (owners to confirm: audit; dashboard)

`Alert` v1.0 — [`alert.mock.json`](examples/alert.mock.json). Built by the backend from an
`AlertCandidate` plus context:

| Field | Source |
|---|---|
| `schema_version`, `alert_id` | backend; `A-<window_end yyyymmddThhmm>Z-<n>`, deterministic |
| `window_end`, `created_at` | window timestamp; server clock |
| `classification` | detector (`leak_suspected`) |
| `region`, `suspected_onset`, `cusum`, `deviating_sensors`, `summary` | detector |
| `hours_since_onset` | backend: `window_end − suspected_onset` |
| `forecast_contributions_m` | forecaster contributions restricted to deviating sensors |
| `models`, `is_mock` | forecaster/detector `ModelInfo`; true if any is a mock |
| `audit` | `{seq, hash}` from the sink, `null` if disabled/failed |

Differences from the design document example (§6), to confirm: `alert_id` format, `audit`
object instead of `audit_seq`, `models` object instead of `model` string,
`forecast_contributions_m` per sensor instead of `xgboost_contributions_m` for one sensor.
The generated narrative is not part of this payload (narrative module).

Audit port:

```python
class AuditSink(Protocol):
    name: str
    enabled: bool
    def append(self, event: AuditEvent) -> AuditRef: ...   # raise AuditError on failure
```

`AuditEvent {type, window_end, alert_id?, payload}` with `type ∈ {alert.raised,
window.scored, window.not_scored}`; one `window.*` event per accepted window (design doc:
"every scored window and every alert"). `alert.raised.payload` = the alert serialised
**without** its `audit` field. `AuditRef {seq, hash?}`.

Requirements for the real sink (audit owner): append-only, sequence assigned by the sink,
`hash = SHA-256(prev_hash ‖ canonical JSON)` (design doc §6), thread-safe, durable before
returning; raise `AuditError` when the event was **not** stored. The backend is
**fail-closed** (Q-AUD-03, ADR-006): an alert is served only after its `alert.raised` event
is recorded; failed events wait in an ordered pending queue, retried before every new window;
a full queue (`SENTINEL_AUDIT_PENDING_CAPACITY`) answers 503 `audit_backlog_full`. The sink
therefore receives events strictly in window order, possibly late, never twice after a
successful append. Shipped sinks: `NullAuditSink` (disabled: alerts served, `audit="disabled"`)
and `InMemoryAuditSink` (dev only, no hash, lost on restart). **No `GET /v1/audit` endpoint** until the audit owner defines the
contract and who may read it (DEP-AUD-02).

## 5. Read API for the dashboard

| Endpoint | Response | Notes |
|---|---|---|
| `GET /health` | `{status, version}` | liveness only |
| `GET /v1/status` | `StatusResponse` | history, registry (provisional flag, units), models (`mocks_active`), `audit_pending_events`, `audit_pending_alerts`, counters, per-stage latency p50/p95/p99, throughput, error rate, `persistence: "memory"` |
| `GET /v1/alerts` | `AlertPage {items, total, limit, offset}` | newest first; `limit` 1–500 (50), `offset`, `since`/`until` (TZ required, on `window_end`, inclusive), `region` |
| `GET /v1/alerts/{id}` | `Alert` | 404 `not_found` |

Errors (all endpoints): `{"error": {"code", "message", "details"}}` —
`validation_error` 422, `sensor_set_mismatch` 422, `timestamp_misaligned` 422,
`timestamp_without_timezone` 422 (only with `SENTINEL_NAIVE_TIMESTAMP_TZ=reject`),
`out_of_order` 409, `window_conflict` 409, `not_found` 404, `audit_backlog_full` 503 (retry
later), `internal_error` 500.

## 6. Versioning and change process

- Additive, optional fields: bump minor (`1.1`) and keep accepting `1.0`.
- Removing/renaming fields, changing units or semantics: new major (`2.0`), new ADR,
  announced in `docs/questions-for-team.md` before merging.
- Every contract change updates: the Pydantic model, this file, `docs/examples/`, tests and
  `CHANGELOG.md` in the same PR.
