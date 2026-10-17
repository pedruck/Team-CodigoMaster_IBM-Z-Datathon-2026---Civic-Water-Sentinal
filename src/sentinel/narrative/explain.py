"""Natural-language explanation of an alert, with a deterministic fallback. Narrative owner (P5).

The language model only rewrites the facts of an alert that the detector already raised; it
never decides whether there is a leak (design document §6). Its text is accepted only if
``check_narrative`` finds nothing outside the payload; otherwise, or if the model fails,
the deterministic template is returned and the reason is recorded.

Two Granite narrators exist: watsonx.ai (``sentinel.narrative.watsonx.WatsonxNarrator``) and
the GGUF model on the LinuxONE VM (``sentinel.narrative.local.LocalGraniteNarrator``). Anything
with ``name`` and ``generate(prompt) -> str`` works (``FunctionNarrator`` wraps a plain
function); an optional ``prompt_for(alert, language)`` replaces the default instruction
prompt. Explanations are in English by default (``DEFAULT_LANGUAGE``).
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from sentinel.audit.chain import alert_payload_sha256
from sentinel.narrative.check import NarrativeCheck, check_narrative
from sentinel.narrative.facts import DEFAULT_LANGUAGE, Language, alert_facts, render_text
from sentinel.schemas.alerts import Alert, AuditRef
from sentinel.schemas.common import ContractModel

log = logging.getLogger("sentinel.narrative")

EXPLANATION_SCHEMA_VERSION: Literal["1.0"] = "1.0"


@runtime_checkable
class Narrator(Protocol):
    @property
    def name(self) -> str: ...

    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class FunctionNarrator:
    """Adapter: ``FunctionNarrator("granite-x@1", lambda prompt: client.generate(prompt))``."""

    name: str
    fn: Callable[[str], str]

    def generate(self, prompt: str) -> str:
        return self.fn(prompt)


class Explanation(ContractModel):
    schema_version: Literal["1.0"] = EXPLANATION_SCHEMA_VERSION
    alert_id: str
    alert_payload_sha256: str  # equals payload_sha256 of the alert's audit record
    audit: AuditRef | None
    is_mock: bool
    language: Language
    source: Literal["template", "llm"]
    generator: str | None  # narrator name when a model was called
    text: str
    facts: dict[str, Any]  # structured explanation; the only input the model sees
    check: NarrativeCheck | None  # validation of the model's text (accepted or rejected)
    fallback_reason: str | None


_PROMPT: dict[Language, str] = {
    "pt": """Você escreve explicações curtas para operadores de uma rede de abastecimento de água.
O motor de detecção já tomou a decisão: o alerta abaixo é uma SUSPEITA de vazamento.
Você não decide, não confirma e não descarta vazamentos.

Regras:
1. Use somente sensores, região, números, datas e horários presentes no JSON.
   Não calcule nem invente valores. Pode arredondar para 1 ou 2 casas decimais.
2. Escreva datas como AAAA-MM-DD e horários em UTC (HH:MM).
3. Se "is_mock" for true, comece com "[SIMULAÇÃO]".
4. No máximo 5 frases, em português, sem listas e sem markdown.
5. Não use expressões como "vazamento confirmado", "com certeza" ou "sem dúvida".

JSON do alerta:
{facts}
""",
    "en": """You write short explanations for operators of a water distribution network.
The detection engine has already made the decision: the alert below is a SUSPECTED leak.
You do not decide, confirm or rule out leaks.

Rules:
1. Use only the sensors, region, numbers, dates and times present in the JSON.
   Do not compute or invent values. You may round to 1 or 2 decimals.
2. Write dates as YYYY-MM-DD and times in UTC (HH:MM).
3. If "is_mock" is true, start with "[SIMULATION]".
4. At most 5 sentences, in English, no lists and no markdown.
5. Do not use phrases such as "confirmed leak", "certainly" or "definitely".

Alert JSON:
{facts}
""",
}


def build_prompt(alert: Alert, language: Language = DEFAULT_LANGUAGE) -> str:
    facts = json.dumps(alert_facts(alert), ensure_ascii=False, indent=2)
    return _PROMPT[language].format(facts=facts)


def explain(
    alert: Alert, narrator: Narrator | None = None, *, language: Language = DEFAULT_LANGUAGE
) -> Explanation:
    """Explanation of ``alert``: the model's text if it passes the check, else the template."""
    template = render_text(alert, language)

    def build(
        source: Literal["template", "llm"],
        text: str,
        check: NarrativeCheck | None = None,
        fallback_reason: str | None = None,
    ) -> Explanation:
        return Explanation(
            alert_id=alert.alert_id,
            alert_payload_sha256=alert_payload_sha256(alert),
            audit=alert.audit,
            is_mock=alert.is_mock,
            language=language,
            source=source,
            generator=narrator.name if narrator else None,
            text=text,
            facts=alert_facts(alert),
            check=check,
            fallback_reason=fallback_reason,
        )

    if narrator is None:
        return build("template", template)
    # A narrator may bring its own prompt (a base model needs examples, not rules).
    prompt_for = getattr(narrator, "prompt_for", None)
    try:
        prompt = prompt_for(alert, language) if callable(prompt_for) else None
        text = narrator.generate(prompt or build_prompt(alert, language)).strip()
    except Exception as exc:  # boundary with an external model: never blocks the explanation
        log.exception("narrator failed", extra={"fields": {"alert_id": alert.alert_id}})
        return build("template", template, fallback_reason=f"narrator_error: {exc!r}")
    if not text:
        return build("template", template, fallback_reason="narrator_empty")
    check = check_narrative(text, alert)
    if not check.ok:
        log.warning(
            "narrative rejected",
            extra={"fields": {"alert_id": alert.alert_id, "check": check.model_dump()}},
        )
        return build("template", template, check, fallback_reason="narrative_rejected")
    return build("llm", text, check)
