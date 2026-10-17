# CivicWater Sentinel — Backend & Ingestion

Ingestion API and orchestration layer of **CivicWater Sentinel** (Team CodigoMaster,
IBM Z Datathon 2026): real-time water-leak detection on 5-minute SCADA windows from the
**simulated** BattLeDIM L-Town network, with auditable inference on IBM LinuxONE.

This module receives each window, validates it, keeps the rolling 12–24 h history, calls the
forecaster and the detector through stable interfaces, stores alerts, emits audit events and
serves results to the dashboard.

> **State:** the backend runs end to end with **mock** forecaster/detector; it validates the
> real BattLeDIM sensor set from the data owner's `data/v1` export. Real models, sensor IDs, audit log and LinuxONE runs are pending
> other members — see [`docs/status.md`](docs/status.md).

## Quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), [just](https://just.systems/).

```bash
uv sync --locked
just check        # format, lint, typecheck (mypy strict), tests
just dev          # http://127.0.0.1:8000/docs
```

Send one window (the example matches the provisional registry):

```bash
curl -s -X POST localhost:8000/v1/windows -H 'content-type: application/json' -d @docs/examples/window.valid.json
```

Replay a synthetic day (invented signal, step drop at window 200). Windows must be newer than
the last accepted one, so start from a fresh server (state is in memory) if you already sent
the example above, which is dated 2019-03-14:

```bash
uv run python scripts/make_synthetic.py data/synthetic.jsonl --count 288 --leak-at 200
```

```bash
just replay data/synthetic.jsonl
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness |
| POST | `/v1/windows` | ingest one window (201 new, 200 idempotent resend, 409 order/conflict, 422 invalid) |
| GET | `/v1/status` | history, models (`mocks_active`), registry, counters, latency p50/p95/p99, throughput, error rate |
| GET | `/v1/alerts` | alerts, newest first; `limit`, `offset`, `since`, `until`, `region` |
| GET | `/v1/alerts/{alert_id}` | one alert |

Contracts: [`docs/integration-contracts.md`](docs/integration-contracts.md); OpenAPI at
`/docs`.

## Configuration

Environment variables `SENTINEL_*`, all with defaults — see [`.env.example`](.env.example).
Main ones: `SENTINEL_HISTORY_CAPACITY` (288), `SENTINEL_MIN_HISTORY_WINDOWS` (144),
`SENTINEL_SENSOR_REGISTRY` (placeholder JSON by default; the data owner's `data/vN/sensors.csv`
for real data), `SENTINEL_AUDIT_SINK` (`null` | `memory`). Invalid values fail at
startup. State is **in memory** and lost on restart (ADR-003).

## Layout

```text
src/sentinel/
  api/            HTTP routes, error envelope (no business rules)
  services/       ingestion validation, history buffer, stores, pipeline
  schemas/        Pydantic contracts (v1.0)
  forecasting/    Forecaster port + mock      (real models: ML owner)
  detection/      Detector port + mock        (CUSUM/localisation: detection owner)
  audit/          AuditSink port + dev sinks  (hash chain: audit owner)
  observability/  metrics, JSON logs
config/           sensor registry (provisional placeholder IDs)
scripts/          synthetic data, replay client, benchmark
docs/             architecture, contracts, ADRs, plan, status, testing, performance
```

## Documentation

[Architecture](docs/architecture.md) · [Contracts](docs/integration-contracts.md) ·
[ADRs](docs/adr/README.md) · [Plan](docs/implementation-plan.md) ·
[Status](docs/status.md) · [Dependencies](docs/dependencies.md) ·
[Questions for the team](docs/questions-for-team.md) · [Testing](docs/testing.md) ·
[Performance](docs/performance.md) · [Agents](AGENTS.md) · [Contributing](CONTRIBUTING.md) ·
[Changelog](CHANGELOG.md)

## Data

No BattLeDIM / L-Town data is stored in this repository; obtain it from its authors under
their terms. Tests and examples use synthetic data with placeholder sensor IDs. The network
is simulated: nothing here is validated on a real distribution network, and mock outputs are
never leak detections.

## License

MIT for the code in this repository — see [`LICENSE`](LICENSE). Third-party data is not
covered.
