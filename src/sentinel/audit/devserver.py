"""Temporary launcher: the backend API with the hash-chained audit log, until the backend
selects it by configuration (BE-013). Uses only the backend's public factories; delete it
once ``SENTINEL_AUDIT_SINK=chain`` exists.

    SENTINEL_AUDIT_RUN=replay-2019 uvicorn sentinel.audit.devserver:create_app --factory

``SENTINEL_AUDIT_RUN`` names the log (default ``local``); ``SENTINEL_AUDIT_DIR`` overrides the
VM location ``~/shared/civicwater/audit`` (e.g. ``data/audit`` on a laptop). Every other
``SENTINEL_*`` variable works as in ``sentinel.main``; ``SENTINEL_AUDIT_SINK`` is ignored.
"""

import logging
import os

from fastapi import FastAPI

from sentinel import main
from sentinel.audit.chain import HashChainAuditSink, audit_path_for
from sentinel.config import Settings

log = logging.getLogger("sentinel.audit")


def create_app() -> FastAPI:
    settings = Settings.from_env()
    path = audit_path_for(
        os.environ.get("SENTINEL_AUDIT_RUN", "local"), os.environ.get("SENTINEL_AUDIT_DIR")
    )
    sink = HashChainAuditSink(path)  # verifies an existing file; refuses a broken one
    app = main.create_app(settings, main.build_pipeline(settings, audit_sink=sink))
    head = sink.head
    log.info(
        "audit chain enabled",
        extra={
            "fields": {
                "path": str(path),
                "head_seq": head.seq if head else None,
                "head_hash": head.hash if head else None,
            }
        },
    )
    return app
