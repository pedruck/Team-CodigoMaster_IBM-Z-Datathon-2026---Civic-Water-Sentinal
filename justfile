# Canonical commands. `just --list` shows them all.

default: check

# Install the locked environment
sync:
    uv sync --locked

fmt:
    uv run ruff format src tests scripts

lint:
    uv run ruff check src tests scripts

typecheck:
    uv run mypy

test *args:
    uv run pytest {{args}}

# All quality gates, in the required order
check: fmt lint typecheck test

# Development server with the mocks (http://127.0.0.1:8000/docs)
dev:
    uv run uvicorn sentinel.main:create_app --factory --reload

# Replay a JSONL file of windows against a running server
replay file url="http://127.0.0.1:8000":
    uv run python scripts/replay.py {{file}} --url {{url}}

# Write a synthetic JSONL day (placeholder sensors, invented signal)
synthetic out="data/synthetic.jsonl" count="288":
    uv run python scripts/make_synthetic.py {{out}} --count {{count}}

# In-process pipeline benchmark with the mocks
bench n="5000":
    uv run python scripts/bench_pipeline.py --windows {{n}}
