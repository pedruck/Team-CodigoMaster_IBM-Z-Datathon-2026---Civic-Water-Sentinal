# ADR-005 — Integration of the real models in process, behind ports

- **Status:** Proposed — needs confirmation by the ML owner (Q-ML-01..05) and the detection
  owner (Q-DET-01..05).
- **Date:** 2026-10-17

## Context
Models are trained off-platform and cross to LinuxONE only as portable files (XGBoost JSON
for Snap ML, GRU in ONNX for zDLC or a PyTorch container) — design doc §4.

## Problem
How the backend loads and calls the real forecaster and detector, and how to avoid a mock
being used silently.

## Decision
- The ML and detection owners deliver Python classes implementing `Forecaster` /
  `Detector` (`docs/integration-contracts.md` §2–§3), plus their artefact files.
- Artefacts are loaded **once at startup** (`build_pipeline`), never per request; startup
  fails if loading fails. Warm-up, if needed, happens at startup.
- Selection by env (`SENTINEL_FORECASTER`, `SENTINEL_DETECTOR`). Today the only accepted
  value is `mock`; unknown values fail at startup. A real value is added only together
  with its implementation, so a mock can never replace a requested real model.
- One detector instance per process; called in order under the pipeline lock.
- Mock usage is visible in every result (`is_mock`) and in `/v1/status.mocks_active`.

## Alternatives considered
- Model server (e.g. HTTP/gRPC inference service): extra hop; contradicts "inference next
  to the data" unless needed for the runtime (PyTorch container) — kept as fallback.
- Loading models lazily on first request: first window pays the cost, failures surface late.

## Pros
No serialisation between backend and models; fail-fast configuration.

## Cons
Model dependencies (Snap ML, onnxruntime/zDLC) become backend runtime dependencies.

## Consequences
Model packages are added as optional dependency groups when delivered, not before.

## Risks
GRU runtime only available in a container (design doc §4) → may force an out-of-process
adapter implementing the same port.

## Dependencies
DEP-ML-01..04, DEP-DET-01..03.

## Revisit when
A runtime cannot be imported in the API process, or model latency requires a separate worker.
