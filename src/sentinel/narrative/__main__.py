"""Explain one alert from the command line; with ``--watsonx``, through Granite on watsonx.ai.

    python -m sentinel.narrative                              # invented alert, template
    python -m sentinel.narrative --watsonx                    # invented alert, Granite
    python -m sentinel.narrative --alert alert.json --watsonx # JSON of GET /v1/alerts/{id}

``--watsonx`` reads the ``WATSONX_*`` variables (see ``sentinel.narrative.watsonx``). Prints
the ``Explanation`` as JSON. Exit code: 0 ok; 1 Granite was asked for but its text was not
used (see ``fallback_reason`` and ``check``); 2 missing configuration.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from sentinel.narrative.explain import Narrator, explain
from sentinel.narrative.facts import DEFAULT_LANGUAGE, Language
from sentinel.narrative.mock import mock_alert
from sentinel.narrative.watsonx import WatsonxError, WatsonxNarrator
from sentinel.schemas.alerts import Alert


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain a CivicWater Sentinel alert.")
    parser.add_argument("--alert", type=Path, help="alert JSON; default: invented mock alert")
    parser.add_argument("--watsonx", action="store_true", help="use Granite on watsonx.ai")
    parser.add_argument("--language", choices=["en", "pt"], default=DEFAULT_LANGUAGE)
    args = parser.parse_args(argv)

    alert = (
        Alert.model_validate_json(args.alert.read_text(encoding="utf-8"))
        if args.alert
        else mock_alert()
    )
    narrator: Narrator | None = None
    if args.watsonx:
        try:
            narrator = WatsonxNarrator.from_env()
        except WatsonxError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    explanation = explain(alert, narrator, language=cast(Language, args.language))
    sys.stdout.write(explanation.model_dump_json(indent=2) + "\n")
    return 1 if args.watsonx and explanation.source != "llm" else 0


if __name__ == "__main__":
    raise SystemExit(main())
