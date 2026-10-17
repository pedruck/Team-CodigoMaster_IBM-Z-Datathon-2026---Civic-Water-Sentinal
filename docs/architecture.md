# Architecture — Backend & Ingestion

Scope of this repository: the **Ingestion API** and the **orchestration** of forecasting,
detection and audit inside CivicWater Sentinel. Source of truth for the overall system is the
team design document *CivicWater Sentinel — Solution design document* (Team CodigoMaster,
IBM Z Datathon 2026, 7 pages, not committed here). Statements below marked **[DD §n]** come
from that document; everything else is a decision of this module, recorded in `docs/adr/`.

## System context [DD §2–§4]

- Input: 5-minute SCADA windows from the simulated BattLeDIM L-Town network:
  33 pressure sensors, 3 flow sensors (two area inflows and the pump) and 1 tank level.
  Each pressure value is the mean of the previous 5 minutes.
- Training on 2018 (off-platform); 2019 replayed as a stream in timestamp order.
- Everything except training runs on IBM LinuxONE (s390x).
- The network is **simulated**. Nothing here is validated on a real network [DD §8].

```mermaid
flowchart LR
  R[Sensor replay<br/>2019, client] -->|POST /v1/windows| API[Ingestion API<br/>this repo]
  API --> H[(History buffer<br/>12–24 h, memory)]
  H --> F[Forecaster<br/>ML owner]
  F -->|expected pressures| D[Detector<br/>detection owner]
  D -->|alert candidates| A[(Alert store)]
  A -->|GET /v1/alerts| UI[Operator dashboard<br/>read-only]
  D -->|window.scored / alert.raised| AU[Audit sink<br/>audit owner]
```

## Components and ownership

| Component | Package | Owner | State |
|---|---|---|---|
| HTTP API (FastAPI) | `sentinel.api` | Backend & Ingestion | planned |
| Contracts (Pydantic) | `sentinel.schemas` | Backend & Ingestion, agreed with each owner | planned |
| Window validation + sensor registry | `sentinel.services.ingestion`, `sentinel.sensors` | Backend & Ingestion | planned |
| History buffer | `sentinel.services.history` | Backend & Ingestion | planned |
| Pipeline / orchestration | `sentinel.services.pipeline` | Backend & Ingestion | planned |
| Forecaster interface + mock | `sentinel.forecasting` | interface: Backend; real models: ML owner | planned (mock) |
| Detector interface + mock | `sentinel.detection` | interface: Backend; CUSUM/localization: detection owner | planned (mock) |
| Audit sink interface | `sentinel.audit` | interface: Backend; hash chain: audit owner | planned (null sink) |
| Metrics | `sentinel.observability` | Backend & Ingestion | planned |
| Narrative model, dashboard, replay of real data | other repos/owners | other members | out of scope here |

Who owns what among the team members is still to be confirmed: see `docs/dependencies.md`.

## Data flow of one window

1. `POST /v1/windows` — Pydantic validates the payload shape (types, finite values,
   timezone, unknown fields).
2. Ingestion validates it against the sensor registry (missing / unexpected sensors) and
   timestamp alignment.
3. Under the pipeline lock: temporal policy against the history (duplicate, conflict,
   out of order, gap) — ADR-004.
4. Window appended to the history buffer (accepted from here on).
5. If the contiguous history is shorter than `min_history_windows`: result
   `insufficient_history`, no forecast.
6. `Forecaster.predict(request)` with **past windows only** plus the current exogenous
   inputs — see `docs/integration-contracts.md` §2.
7. `Detector.evaluate(observed, expected, timestamp)` — the single long-lived detector
   instance keeps CUSUM state across windows.
8. Alert candidates become versioned `Alert` records in the alert store.
9. Audit events (`window.scored`, `alert.raised`) are sent to the audit sink.
10. A `WindowResult` is stored for idempotent replays and returned to the client.

Failure handling, idempotency and the status vocabulary: ADR-006 and
`docs/integration-contracts.md` §5.

## Non-goals of this stage

- Durable persistence (history, alerts and results are lost on restart — ADR-003).
- Asynchronous queues or distributed processing (ADR-002).
- Real forecasting, CUSUM, localization, hash chain or narrative (other owners).
