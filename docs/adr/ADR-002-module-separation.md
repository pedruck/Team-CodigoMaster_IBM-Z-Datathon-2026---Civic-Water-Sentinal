# ADR-002 — Ports between API, history, forecasting, detection and audit

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Backend & Ingestion owner.

## Context
Forecasting (ML owner), detection/localisation (detection owner) and the audit log (audit
owner) are developed in parallel by different people and AI agents.

## Problem
Let each module evolve independently without the HTTP layer knowing model details, and keep
the backend testable before the real modules exist.

## Decision
- Routes (`sentinel.api`) only translate HTTP ↔ `Pipeline` calls; no business rules.
- `Pipeline` (`sentinel.services.pipeline`) owns the processing order and failure policy.
- `Forecaster`, `Detector` and `AuditSink` are `typing.Protocol` ports with Pydantic
  input/output models (`docs/integration-contracts.md`).
- Mocks implement the same ports and declare `is_mock=True`, which propagates to results,
  alerts and `/v1/status`.
- Synchronous, in-process processing: one window is fully processed inside the request.
  No queues or workers.

## Alternatives considered
- Separate microservices per model: network hops and deployment cost without a measured need;
  the design document wants inference next to the data.
- Async queue (ingest now, score later): adds ordering/retry complexity the 5-minute cadence
  does not need.

## Pros
Each owner implements one class; the backend tests every failure path with doubles.

## Cons
A slow model blocks the request (and the pipeline lock) for its duration.

## Consequences
The real modules are imported as Python packages and wired in `sentinel.main.build_pipeline`
(ADR-005).

## Risks
Owners may need extra inputs not in the contract (e.g. calendar features): handled by
versioning the contract.

## Dependencies
Contract confirmation by ML, detection and audit owners (Q-ML-01, Q-DET-01, Q-AUD-01).

## Revisit when
Measured model latency approaches the window interval, or a module must run out of process.
