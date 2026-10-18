# ADR-005 — Integration of the real models in process, behind ports

- **Status:** Accepted for the forecaster (ML owner's answers Q-ML-01..06 in
  `docs/p2-forecasting.md`, wired in BE-011); Proposed for the detector (Q-DET-01..05).
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
- Selection by env (`SENTINEL_FORECASTER`, `SENTINEL_DETECTOR`); unknown values fail at
  startup. A real value is added only together with its implementation, so a mock can never
  replace a requested real model. Forecaster (BE-011, 2026-10-17): `mock | baseline |
  xgboost | gru`, artefact in `SENTINEL_FORECASTER_PATH`, loaded with the ML owner's
  `load_forecaster`; a load error stops the process. XGBoost options:
  `SENTINEL_FORECASTER_CONTRIBUTIONS` (TreeSHAP: `on_alert` default | `all` | `off`;
  `on_alert` asks the optional `ExplainingForecaster.explain` after detection, contract §2),
  `_ENGINE`, `_TREE_FORMAT`
  (`auto | compress_trees | zdnn_tensors`). Detector: `mock` and `cusum` (ADR-007, ADR-008).
- Artefact integrity (2026-10-18): `load_forecaster` computes the artefact's SHA-256
  (`sentinel.forecasting.integrity`: the file, or every non-hidden file of the directory by
  relative path) **before** parsing it. The served `ModelInfo.version` carries the first 8 hex
  digits (`0.1.0+808746bc`), so the audit names the exact weights; the full digest is logged.
  `SENTINEL_FORECASTER_SHA256` pins it: any other artefact stops startup. A calibration naming
  only the release (`0.1.0`) matches any artefact of that release; one naming `0.1.0+<digest>`
  matches only that artefact (ADR-008). Limit: a file swapped after startup is not detected
  until the next start.
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
Model packages are added as optional dependency groups when delivered, not before. Until
then (DEP-ML-03) the real forecasters import numpy/xgboost/snapml lazily and run only with
the VM image's Python (`/opt/venv`); CI runs their tests where the libraries exist.

## Risks
GRU runtime only available in a container (design doc §4) → may force an out-of-process
adapter implementing the same port.

## Dependencies
DEP-ML-01..04, DEP-DET-01..03.

## Revisit when
A runtime cannot be imported in the API process, or model latency requires a separate worker.
