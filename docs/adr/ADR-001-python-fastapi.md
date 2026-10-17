# ADR-001 — Python + FastAPI for the application layer

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Backend & Ingestion owner; consistent with the team design document (Table 3: "Ingestion API — LinuxONE (FastAPI)").

## Context
The design document fixes FastAPI on LinuxONE (s390x) for the Ingestion API. The models it
serves are Python artefacts (Snap ML for XGBoost, ONNX/zDLC or PyTorch for the GRU).

## Problem
Choose the language/framework of the API and orchestration layer.

## Decision
Python 3.12, FastAPI, Pydantic v2 for contracts, uvicorn as server, uv for dependencies.

## Alternatives considered
- Flask + marshmallow: no typed contracts/OpenAPI out of the box.
- Rust/Go service calling Python models: cross-language boundary for every window, no
  measured need (see `docs/performance.md`).

## Pros
Same language as the models (in-process calls, no serialisation); OpenAPI generated from
the contracts; Pydantic validation is declarative and testable.

## Cons
GIL: CPU-bound model inference serialises with request handling. Python wheels for s390x
are not guaranteed for every package.

## Consequences
Contracts live in `sentinel.schemas`; OpenAPI at `/docs` is the HTTP contract reference.

## Risks
`pydantic-core` / `uvicorn` / model runtimes without s390x wheels (design doc Table 5).
Mitigation: verify on the LinuxONE VM early (DEP-PLAT-01).
Observed 2026-10-17: all runtime dependencies install from s390x wheels and the test suite
passes; only the dev tool `mypy` fails (its dependency `librt` needs a C compiler).

## Dependencies
LinuxONE VM image (platform owner).

## Revisit when
s390x installation fails, or measured p99 per window on LinuxONE exceeds the 5-minute budget
by a margin that the backend (not the models) explains.
