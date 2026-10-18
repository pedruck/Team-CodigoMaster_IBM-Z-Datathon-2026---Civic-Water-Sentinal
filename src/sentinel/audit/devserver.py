"""Compatibility launcher for the audit owner's commands (``docs/audit-and-narrative.md``).

Since BE-013 the backend selects the chain and the explanations by configuration::

    SENTINEL_AUDIT_SINK=chain SENTINEL_AUDIT_RUN=replay-2019 \\
        uvicorn sentinel.main:create_app --factory

This module only keeps the earlier command working: it is ``sentinel.main.create_app`` with
``SENTINEL_AUDIT_SINK=chain`` forced and ``SENTINEL_AUDIT_RUN`` defaulting to ``local``.

    SENTINEL_AUDIT_RUN=replay-2019 uvicorn sentinel.audit.devserver:create_app --factory

``SENTINEL_AUDIT_DIR`` and ``SENTINEL_NARRATOR`` work as in ``sentinel.main``.
"""

import os

from fastapi import FastAPI

from sentinel import main
from sentinel.config import Settings
from sentinel.narrative.runtime import ExplainingAuditSink, narrator_for, narrator_from_env

__all__ = ["ExplainingAuditSink", "create_app", "narrator_for", "narrator_from_env"]


def create_app() -> FastAPI:
    env = {
        **os.environ,
        "SENTINEL_AUDIT_SINK": "chain",
        "SENTINEL_AUDIT_RUN": os.environ.get("SENTINEL_AUDIT_RUN", "local"),
    }
    return main.create_app(Settings.from_env(env))
