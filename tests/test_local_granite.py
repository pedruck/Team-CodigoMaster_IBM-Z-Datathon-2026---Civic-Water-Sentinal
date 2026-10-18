"""Granite GGUF on the VM (P5) against a fake llama.cpp completions server. No model and no
network here: the real model on the LinuxONE was not run by these tests."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest

from sentinel.narrative import (
    LlamaCppNarrator,
    LocalGraniteNarrator,
    LocalLLMConfig,
    LocalLLMError,
    Narrator,
    explain,
    render_text,
)
from sentinel.narrative.__main__ import main as cli_main
from sentinel.narrative.local import DEFAULT_MODEL, build_completion_prompt, prompt_facts
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
    assert json.loads(last_record) == prompt_facts(alert)
    assert "null" not in last_record and 'deviation_m": -0.91' in last_record
    for distractor in ("top_contributions_m", "detector_summary", "audit_seq", "models"):
        assert distractor not in last_record
    # The simulation notice is the code's job (explain adds it), not the model's.
    assert "is_mock" not in prompt and "[SIMULATION]" not in prompt
    pt = build_completion_prompt(alert, "pt")
    assert pt.endswith("Explicação:") and "[SIMULAÇÃO]" not in pt and "região R2" in pt


def test_examples_have_the_same_shape_as_the_alert() -> None:
    """On the VM the model copied the region of an example into an alert without one."""
    unlocated = build_completion_prompt(mock_alert(region=None))
    records = [line for line in unlocated.splitlines() if line.startswith("Record:")]
    assert len(records) == 3 and all('"region"' not in r for r in records)
    assert "R2" not in unlocated and "R7" not in unlocated
    located = build_completion_prompt(mock_alert())
    assert all('"region"' in r for r in located.splitlines() if r.startswith("Record:"))


def test_explain_adds_the_simulation_notice_the_model_left_out() -> None:
    plain = GOOD.removeprefix("[SIMULATION] ")
    e = explain(mock_alert(), narrator(FakeLlamaServer(plain)))
    assert e.source == "llm" and e.text == "[SIMULATION] " + plain
    assert e.model_text == plain
    real = explain(mock_alert(is_mock=False), narrator(FakeLlamaServer(plain)))
    assert real.text == plain  # no notice on a real alert


def test_explain_sends_the_few_shot_prompt_to_the_completions_endpoint() -> None:
    fake = FakeLlamaServer()
    alert = mock_alert()
    e = explain(alert, narrator(fake))
    assert e.source == "llm" and e.text == GOOD and e.generator == "local:" + DEFAULT_MODEL
    url, body, timeout_s = fake.calls[0]
    assert url == "http://127.0.0.1:8080/v1/completions"
    assert body["prompt"] == build_completion_prompt(alert)
    assert body["temperature"] == 0 and body["max_tokens"] == 120
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


# ------------------------------------------------- in-process llama_cpp (LlamaCppNarrator)


class FakeLlama:
    """Stands in for ``llama_cpp.Llama``: records the load and every completion."""

    instances: ClassVar[list["FakeLlama"]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.prompts: list[str] = []
        self.closed = False
        FakeLlama.instances.append(self)

    def create_completion(self, prompt: str, **kwargs: Any) -> Any:
        self.prompts.append(prompt)
        self.completion_kwargs = kwargs
        return {
            "choices": [{"text": " " + GOOD}],
            "usage": {"prompt_tokens": 412, "completion_tokens": 58, "total_tokens": 470},
        }

    def close(self) -> None:
        self.closed = True


def test_llamacpp_loads_once_with_safe_defaults(tmp_path: Path) -> None:
    model = tmp_path / "granite-be.f16.gguf"
    model.write_bytes(b"GGUF")
    FakeLlama.instances.clear()
    n = LlamaCppNarrator(model, llama_factory=FakeLlama)
    assert isinstance(n, Narrator) and n.name == "llamacpp:granite-be.f16.gguf"
    assert not n.loaded

    alert = mock_alert()
    for _ in range(2):
        e = explain(alert, n)
        assert e.source == "llm" and e.text == GOOD
    (llm,) = FakeLlama.instances  # loaded once, reused
    assert llm.kwargs == {
        "model_path": str(model),
        "n_ctx": 2048,
        "n_threads": 1,
        "seed": 0,
        "verbose": False,
    }
    assert llm.prompts[0] == build_completion_prompt(alert)
    assert llm.completion_kwargs["temperature"] == 0.0
    assert "\n\n" in llm.completion_kwargs["stop"]
    assert n.last_usage == {"prompt_tokens": 412, "completion_tokens": 58, "total_tokens": 470}

    n.close()
    assert llm.closed and not n.loaded


def test_llamacpp_from_env(tmp_path: Path) -> None:
    with pytest.raises(LocalLLMError, match="model file not found"):
        LlamaCppNarrator.from_env({"LOCAL_LLM_MODEL_PATH": str(tmp_path / "missing.gguf")})
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    n = LlamaCppNarrator.from_env(
        {"LOCAL_LLM_MODEL_PATH": str(model), "LOCAL_LLM_THREADS": "2", "LOCAL_LLM_CTX": "1024"}
    )
    assert (n.model_path, n.n_threads, n.n_ctx) == (model, 2, 1024)
    with pytest.raises(LocalLLMError, match="integers"):
        LlamaCppNarrator.from_env({"LOCAL_LLM_MODEL_PATH": str(model), "LOCAL_LLM_THREADS": "x"})


def test_llamacpp_load_failure_falls_back(tmp_path: Path) -> None:
    def broken(**kwargs: Any) -> Any:
        raise ValueError("unknown model architecture: 'granitehybrid'")

    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    e = explain(mock_alert(), LlamaCppNarrator(model, llama_factory=broken))
    assert e.source == "template" and e.fallback_reason is not None
    assert "granitehybrid" in e.fallback_reason


def test_cli_llamacpp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setattr(
        LlamaCppNarrator,
        "from_env",
        classmethod(lambda cls: cls(model, llama_factory=FakeLlama)),
    )
    assert cli_main(["--llamacpp"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source"] == "llm" and out["generator"] == "llamacpp:m.gguf"
    assert "model_load_s" in out and "elapsed_s" in out
    assert out["usage"]["prompt_tokens"] == 412

    monkeypatch.setattr(
        LlamaCppNarrator, "from_env", classmethod(lambda cls: cls(tmp_path / "x.gguf"))
    )
    assert cli_main(["--llamacpp"]) == 2  # llama_cpp missing here, or the file does not exist
