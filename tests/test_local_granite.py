"""Granite GGUF on the VM (P5) against a fake llama.cpp completions server. No model and no
network here: the real model on the LinuxONE was not run by these tests."""

import json
from collections.abc import Mapping
from typing import Any

import pytest

from sentinel.narrative import (
    LocalGraniteNarrator,
    LocalLLMConfig,
    LocalLLMError,
    Narrator,
    explain,
    render_text,
)
from sentinel.narrative.__main__ import main as cli_main
from sentinel.narrative.local import DEFAULT_MODEL, build_completion_prompt, compact_facts
from sentinel.narrative.mock import mock_alert

GOOD = (
    "[SIMULATION] Alert A-20190314T1230Z-1 reports a suspected leak in region R4. "
    "Sensors P14 and P21 read 0.91 m and 0.62 m below the expected pressure since "
    "2019-03-14 02:10 UTC."
)


class FakeLlamaServer:
    def __init__(self, text: str = GOOD) -> None:
        self.text = text
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> Any:
        self.calls.append((url, json.loads(body), timeout_s))
        return {"choices": [{"text": " " + self.text + "\n"}]}


def narrator(fake: FakeLlamaServer) -> LocalGraniteNarrator:
    return LocalGraniteNarrator(LocalLLMConfig(), post=fake)


def test_config_defaults_and_environment() -> None:
    assert LocalLLMConfig.from_env({}) == LocalLLMConfig()
    c = LocalLLMConfig.from_env(
        {
            "LOCAL_LLM_URL": "http://10.0.0.5:9000/",
            "LOCAL_LLM_MODEL": "granite-x",
            "LOCAL_LLM_TIMEOUT_S": "5",
        }
    )
    assert (c.url, c.model, c.timeout_s) == ("http://10.0.0.5:9000", "granite-x", 5.0)
    with pytest.raises(LocalLLMError):
        LocalLLMConfig.from_env({"LOCAL_LLM_TIMEOUT_S": "soon"})
    n = LocalGraniteNarrator()
    assert isinstance(n, Narrator) and n.name == "local:granite-4.0-h-350m-base"


def test_few_shot_prompt_ends_with_the_alert() -> None:
    alert = mock_alert()
    prompt = build_completion_prompt(alert)
    assert prompt.count("Record:") == 3 and prompt.endswith("Explanation:")
    last_record = prompt.splitlines()[-2].removeprefix("Record: ")
    assert json.loads(last_record) == compact_facts(alert)
    assert "audit_seq" in last_record and "null" not in last_record
    pt = build_completion_prompt(alert, "pt")
    assert pt.endswith("Explicação:") and "[SIMULAÇÃO]" in pt


def test_explain_sends_the_few_shot_prompt_to_the_completions_endpoint() -> None:
    fake = FakeLlamaServer()
    alert = mock_alert()
    e = explain(alert, narrator(fake))
    assert e.source == "llm" and e.text == GOOD and e.generator == "local:" + DEFAULT_MODEL
    url, body, timeout_s = fake.calls[0]
    assert url == "http://127.0.0.1:8080/v1/completions"
    assert body["prompt"] == build_completion_prompt(alert)
    assert body["temperature"] == 0 and body["max_tokens"] == 160
    assert "\n\n" in body["stop"]
    assert timeout_s == 60.0


def test_unfinished_sentence_is_cut() -> None:
    fake = FakeLlamaServer(GOOD + " The CUSUM statistic reached")
    assert narrator(fake).generate("p") == GOOD


def test_example_copied_by_the_model_is_rejected() -> None:
    """A small base model may echo the few-shot example: the check catches it."""
    alert = mock_alert()
    echo = (
        "[SIMULATION] Alert A-20180105T0300Z-1 reports a suspected leak in region R2. "
        "Sensors P05 and P11 read 0.74 m and 0.52 m below the expected pressure."
    )
    e = explain(alert, narrator(FakeLlamaServer(echo)))
    assert e.source == "template" and e.text == render_text(alert)
    assert e.check is not None and "P05" in e.check.unknown_references


def test_server_down_or_bad_answer_falls_back() -> None:
    def down(url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> Any:
        raise LocalLLMError("request to 127.0.0.1:8080 failed: connection refused")

    e = explain(mock_alert(), LocalGraniteNarrator(post=down))
    assert e.source == "template" and e.fallback_reason is not None
    assert "connection refused" in e.fallback_reason

    def weird(url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> Any:
        return {"content": "llama.cpp native format"}

    with pytest.raises(LocalLLMError, match="choices"):
        LocalGraniteNarrator(post=weird).generate("p")


def test_cli_local(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeLlamaServer()
    monkeypatch.setattr(LocalGraniteNarrator, "from_env", classmethod(lambda cls: narrator(fake)))
    assert cli_main(["--local"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source"] == "llm" and "elapsed_s" in out

    fake.text = "[SIMULATION] Sensor P99 failed."
    assert cli_main(["--local"]) == 1
    assert json.loads(capsys.readouterr().out)["fallback_reason"] == "narrative_rejected"

    with pytest.raises(SystemExit):
        cli_main(["--local", "--watsonx"])
