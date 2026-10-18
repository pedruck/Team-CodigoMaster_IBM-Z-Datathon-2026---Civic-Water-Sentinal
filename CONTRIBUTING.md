# Contributing

Humans and AI agents follow the same rules; agents also follow `AGENTS.md`.

## Setup

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), [just](https://just.systems/).

```bash
uv sync --locked
just check
```

## Workflow

1. Pick or add a task in `docs/implementation-plan.md` (`BE-NNN`).
2. Branch from `main`: `feat/be-011-xgboost-forecaster`, `fix/…`, `docs/…`.
3. Implement with tests; keep the change small.
4. `just check` must be green.
5. Update `docs/status.md`, the plan, `CHANGELOG.md` (`Unreleased`) and, when affected,
   ADRs, contracts and `docs/examples/`.
6. Open a PR with the completion report from `AGENTS.md` §4 as description.

## Commits

Conventional Commits in English: `feat(api): add region filter to alerts`. Subject ≤ 72
chars, imperative. One logical change per commit.

## Contracts

The contracts in `docs/integration-contracts.md` are shared with other modules. Additive
optional fields → minor version; anything else → major version + ADR + announcement in
`docs/questions-for-team.md` before merging.

## Integrating your module (ML, detection, audit owners)

1. Implement the port (`Forecaster`, `Detector` or `AuditSink`) in a package or under
   `src/sentinel/<area>/`.
2. Make it pass the existing pipeline/API tests by injecting it through
   `sentinel.main.build_pipeline(...)`.
3. Add a named option to `SENTINEL_FORECASTER` / `SENTINEL_DETECTOR` / `SENTINEL_AUDIT_SINK`
   in `sentinel/config.py` and wire it in `build_pipeline` (forecasters: `config.FORECASTERS`
   + `forecasting/registry.py`).
4. Add its runtime dependencies as an optional group in `pyproject.toml`.

## Never commit

Secrets, `.env`, BattLeDIM/SCADA data, model artefacts (`.onnx`, `.ubj`, `.pkl`), generated
JSONL replays. The pre-commit hook runs `gitleaks`; do not bypass it.

Exception: the dashboard playback fixtures in `dashboard/playback/*/` (a few KB, mock backend
on a synthetic day, regenerated with `just playback-fixtures`) are committed because the tests
and the demo fallback read them. They are not BattLeDIM data and every alert is `is_mock`.
