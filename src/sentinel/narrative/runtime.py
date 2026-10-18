"""Wiring of the stored explanations into the backend (BE-013, Q-AUD-07). Audit owner's code
(P5), moved from the temporary ``sentinel.audit.devserver`` so that ``sentinel.main`` selects
it by configuration.

- ``ExplainingAuditSink`` wraps the hash chain: after an ``alert.raised`` event is recorded,
  the alert is queued on the ``ExplanationService``.
- ``narrator_for`` builds the narrator named by ``SENTINEL_NARRATOR``; the model settings
  (``LOCAL_LLM_*``, ``WATSONX_*``) are read from the environment, never from ``Settings``.
"""

import logging
import os
from collections.abc import Mapping

from sentinel.audit.chain import HashChainAuditSink
from sentinel.config import NARRATORS, ConfigError
from sentinel.narrative.explain import Narrator
from sentinel.narrative.local import LlamaCppNarrator, LocalGraniteNarrator, LocalLLMError
from sentinel.narrative.store import ExplanationService
from sentinel.narrative.watsonx import WatsonxError, WatsonxNarrator
from sentinel.schemas.alerts import Alert, AuditEvent, AuditRef

log = logging.getLogger("sentinel.audit")


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


def narrator_for(choice: str, env: Mapping[str, str] | None = None) -> Narrator | None:
    """The narrator named ``choice`` (``none`` → ``None``: deterministic text only). Fails at
    startup, not on the first alert."""
    e = os.environ if env is None else env
    if choice not in NARRATORS:
        raise ConfigError(f"SENTINEL_NARRATOR must be one of {sorted(NARRATORS)}, got {choice!r}")
    try:
        if choice == "llamacpp":
            narrator = LlamaCppNarrator.from_env(e)
            narrator.load()
            return narrator
        if choice == "local":
            return LocalGraniteNarrator.from_env(e)
        if choice == "watsonx":
            return WatsonxNarrator.from_env(e)
    except (LocalLLMError, WatsonxError) as exc:
        raise ConfigError(f"SENTINEL_NARRATOR={choice}: {exc}") from exc
    return None


def narrator_from_env(env: Mapping[str, str] | None = None) -> Narrator | None:
    e = os.environ if env is None else env
    return narrator_for(e.get("SENTINEL_NARRATOR", "none"), e)
