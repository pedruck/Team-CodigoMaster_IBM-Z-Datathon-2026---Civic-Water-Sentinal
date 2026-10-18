"""Temporary launcher: the backend API with P5's hash-chained audit log and stored, audited
explanations, until the backend selects them by configuration (BE-013) and exposes the
explanation (Q-AUD-07). Uses only the backend's public factories and error types; delete it
once those exist in ``sentinel.main`` / ``sentinel.api``.

    SENTINEL_AUDIT_RUN=replay-2019 uvicorn sentinel.audit.devserver:create_app --factory

- ``SENTINEL_AUDIT_RUN`` names the logs (default ``local``): ``<run>.jsonl`` (audit) and
  ``<run>.explanations.jsonl`` (explanations), in ``~/shared/civicwater/audit`` or in
  ``SENTINEL_AUDIT_DIR`` (e.g. ``data/audit`` on a laptop).
- ``SENTINEL_NARRATOR``: ``none`` (default, deterministic text only), ``llamacpp`` (Granite GGUF
  in this process), ``local`` (llama.cpp server) or ``watsonx``; see ``sentinel.narrative``.

Every alert recorded in the audit log is explained in the background and the result is
appended to the explanations log (English, like the rest of the project).
``GET /v1/alerts/{alert_id}/explanation`` returns ``pending`` (deterministic text meanwhile)
or ``ready``. Every other ``SENTINEL_*`` variable works as in ``sentinel.main``;
``SENTINEL_AUDIT_SINK`` is ignored.
"""

import logging
import os
from collections.abc import Mapping

from fastapi import FastAPI

from sentinel import main
from sentinel.audit.chain import HashChainAuditSink, audit_path_for
from sentinel.config import ConfigError, Settings
from sentinel.narrative.explain import Narrator
from sentinel.narrative.local import LlamaCppNarrator, LocalGraniteNarrator, LocalLLMError
from sentinel.narrative.store import (
    ExplanationService,
    ExplanationStatus,
    ExplanationStore,
    explanations_path_for,
)
from sentinel.narrative.watsonx import WatsonxError, WatsonxNarrator
from sentinel.schemas.alerts import Alert, AuditEvent, AuditRef
from sentinel.services.errors import NotFoundError

log = logging.getLogger("sentinel.audit")

NARRATORS = ("none", "llamacpp", "local", "watsonx")


class ExplainingAuditSink:
    """``AuditSink`` wrapper: after an ``alert.raised`` event is recorded, queue its
    explanation. Queuing can never fail the append: the backend is fail-closed and would
    retry an event that is already in the log."""

    def __init__(self, inner: HashChainAuditSink, service: ExplanationService) -> None:
        self.inner = inner
        self.service = service
        self.name = inner.name
        self.enabled = inner.enabled

    def append(self, event: AuditEvent) -> AuditRef:
        ref = self.inner.append(event)
        if event.type == "alert.raised":
            try:
                self.service.submit(
                    Alert.model_validate({**event.payload, "audit": ref.model_dump()})
                )
            except Exception:
                log.exception("could not queue the explanation", extra={"fields": {"seq": ref.seq}})
        return ref


def narrator_from_env(env: Mapping[str, str] | None = None) -> Narrator | None:
    e = os.environ if env is None else env
    choice = e.get("SENTINEL_NARRATOR", "none")
    if choice not in NARRATORS:
        raise ConfigError(f"SENTINEL_NARRATOR must be one of {list(NARRATORS)}, got {choice!r}")
    try:
        if choice == "llamacpp":
            narrator = LlamaCppNarrator.from_env(e)
            narrator.load()  # fail at start-up, not on the first alert
            return narrator
        if choice == "local":
            return LocalGraniteNarrator.from_env(e)
        if choice == "watsonx":
            return WatsonxNarrator.from_env(e)
    except (LocalLLMError, WatsonxError) as exc:
        raise ConfigError(f"SENTINEL_NARRATOR={choice}: {exc}") from exc
    return None


def create_app() -> FastAPI:
    settings = Settings.from_env()
    run = os.environ.get("SENTINEL_AUDIT_RUN", "local")
    root = os.environ.get("SENTINEL_AUDIT_DIR")
    audit = HashChainAuditSink(audit_path_for(run, root))  # verifies; refuses a broken file
    explanations = ExplanationService(
        narrator_from_env(),
        ExplanationStore(explanations_path_for(run, root)),
    )
    sink = ExplainingAuditSink(audit, explanations)
    app = main.create_app(settings, main.build_pipeline(settings, audit_sink=sink))
    app.state.explanations = explanations

    @app.get(
        "/v1/alerts/{alert_id}/explanation",
        response_model=ExplanationStatus,
        tags=["alerts"],
        summary="Explanation of an alert (P5, reference for Q-AUD-07)",
    )
    def get_explanation(alert_id: str) -> ExplanationStatus:
        status = explanations.status(alert_id)
        if status is None:
            raise NotFoundError(f"no explanation for alert {alert_id!r}")
        return status

    head = audit.head
    log.info(
        "audit chain and explanations enabled",
        extra={
            "fields": {
                "audit_path": str(audit.path),
                "explanations_path": str(explanations.store.path),
                "narrator": explanations.narrator_name,
                "head_seq": head.seq if head else None,
                "head_hash": head.hash if head else None,
            }
        },
    )
    return app
