"""Explain one alert from the command line, optionally through Granite.

    python -m sentinel.narrative                                # invented alert, template
    python -m sentinel.narrative --llamacpp                     # GGUF loaded in this process
    python -m sentinel.narrative --local                        # GGUF behind a llama.cpp server
    python -m sentinel.narrative --watsonx                      # Granite on watsonx.ai
    python -m sentinel.narrative --alert alert.json --llamacpp  # JSON of GET /v1/alerts/{id}

``--llamacpp`` and ``--local`` read ``LOCAL_LLM_*`` (see ``sentinel.narrative.local``);
``--watsonx`` reads ``WATSONX_*`` (see ``sentinel.narrative.watsonx``). Prints the
``Explanation`` as JSON with the generation time (and the model load time for
``--llamacpp``). Exit code: 0 ok; 1 Granite was asked for but its text was not used (see
``fallback_reason`` and ``check``); 2 missing configuration or model that does not load.
"""

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from sentinel.narrative.explain import Narrator, explain
from sentinel.narrative.local import LlamaCppNarrator, LocalGraniteNarrator, LocalLLMError
from sentinel.narrative.mock import mock_alert
from sentinel.narrative.watsonx import WatsonxError, WatsonxNarrator
from sentinel.schemas.alerts import Alert


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain a CivicWater Sentinel alert.")
    parser.add_argument("--alert", type=Path, help="alert JSON; default: invented mock alert")
    model = parser.add_mutually_exclusive_group()
    model.add_argument("--llamacpp", action="store_true", help="load the GGUF in this process")
    model.add_argument("--local", action="store_true", help="GGUF behind a llama.cpp server")
    model.add_argument("--watsonx", action="store_true", help="Granite on watsonx.ai")
    args = parser.parse_args(argv)

    alert = (
        Alert.model_validate_json(args.alert.read_text(encoding="utf-8"))
        if args.alert
        else mock_alert()
    )
    narrator: Narrator | None = None
    load_s: float | None = None
    try:
        if args.llamacpp:
            in_process = LlamaCppNarrator.from_env()
            t = time.perf_counter()
            in_process.load()
            load_s = round(time.perf_counter() - t, 3)
            narrator = in_process
        elif args.local:
            narrator = LocalGraniteNarrator.from_env()
        elif args.watsonx:
            narrator = WatsonxNarrator.from_env()
    except (LocalLLMError, WatsonxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    started = time.perf_counter()
    explanation = explain(alert, narrator)
    report = explanation.model_dump(mode="json")
    report["elapsed_s"] = round(time.perf_counter() - started, 3)
    if load_s is not None:
        report["model_load_s"] = load_s
    if isinstance(narrator, LlamaCppNarrator):
        report["usage"] = narrator.last_usage
    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 1 if narrator is not None and explanation.source != "llm" else 0


if __name__ == "__main__":
    raise SystemExit(main())
