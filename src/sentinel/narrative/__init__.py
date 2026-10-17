"""Explicabilidade (P5): fatos estruturados do alerta, texto determinístico, validação e
narrativa gerada por modelo de linguagem (watsonx.ai / Granite) com fallback. Não decide se
há vazamento. Idioma padrão: inglês."""

from sentinel.narrative.check import NarrativeCheck, check_narrative
from sentinel.narrative.explain import (
    Explanation,
    FunctionNarrator,
    Narrator,
    build_prompt,
    explain,
)
from sentinel.narrative.facts import DEFAULT_LANGUAGE, Language, alert_facts, render_text
from sentinel.narrative.watsonx import WatsonxConfig, WatsonxError, WatsonxNarrator

__all__ = [
    "DEFAULT_LANGUAGE",
    "Explanation",
    "FunctionNarrator",
    "Language",
    "NarrativeCheck",
    "Narrator",
    "WatsonxConfig",
    "WatsonxError",
    "WatsonxNarrator",
    "alert_facts",
    "build_prompt",
    "check_narrative",
    "explain",
    "render_text",
]
