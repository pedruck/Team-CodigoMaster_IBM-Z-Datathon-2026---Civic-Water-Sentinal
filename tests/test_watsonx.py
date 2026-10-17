"""watsonx.ai narrator (P5) against a fake HTTP transport. No network, no credentials:
the real service was never called by these tests."""

import io
import json
import urllib.error
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from sentinel.narrative import (
    Narrator,
    WatsonxConfig,
    WatsonxError,
    WatsonxNarrator,
    explain,
    render_text,
)
from sentinel.narrative.__main__ import main as cli_main
from sentinel.narrative.mock import mock_alert
from sentinel.narrative.watsonx import IAM_URL, http_post

ENV = {
    "WATSONX_APIKEY": "fake-key-for-tests",
    "WATSONX_URL": "https://example.ml.cloud.ibm.com/",
    "WATSONX_PROJECT_ID": "project-123",
    "WATSONX_MODEL_ID": "ibm/granite-example",
}
TEXT = (
    "[SIMULATION] Alert A-20190314T1230Z-1 reports a suspected leak in region R4. "
    "Sensors P14 and P21 read 0.91 m and 0.62 m below the expected pressure since "
    "2019-03-14 02:10 UTC."
)


class FakeWatsonx:
    """Records every request and answers like IAM / the chat API."""

    def __init__(self, reply: Any = None, expires_in: float = 3600.0) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes, float]] = []
        self.reply = reply if reply is not None else {"choices": [{"message": {"content": TEXT}}]}
        self.expires_in = expires_in

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> Any:
        self.calls.append((url, dict(headers), body, timeout_s))
        if url == IAM_URL:
            n = sum(1 for c in self.calls if c[0] == IAM_URL)
            return {"access_token": f"token-{n}", "expires_in": self.expires_in}
        return self.reply

    def chat_calls(self) -> list[tuple[str, dict[str, str], bytes, float]]:
        return [c for c in self.calls if c[0] != IAM_URL]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def narrator(fake: FakeWatsonx, clock: Clock | None = None) -> WatsonxNarrator:
    return WatsonxNarrator(WatsonxConfig.from_env(ENV), post=fake, clock=clock or Clock())


def test_config_reads_the_environment_and_hides_the_key() -> None:
    config = WatsonxConfig.from_env({**ENV, "WATSONX_TIMEOUT_S": "5"})
    assert config.url == "https://example.ml.cloud.ibm.com"
    assert config.timeout_s == 5.0
    assert "fake-key-for-tests" not in repr(config)
    n = WatsonxNarrator(config)
    assert isinstance(n, Narrator) and n.name == "watsonx:ibm/granite-example"


def test_missing_configuration_names_the_variables_only() -> None:
    with pytest.raises(WatsonxError, match="WATSONX_PROJECT_ID, WATSONX_MODEL_ID") as exc:
        WatsonxConfig.from_env({"WATSONX_APIKEY": "secret", "WATSONX_URL": "https://x"})
    assert "secret" not in str(exc.value)


def test_chat_request_follows_the_watsonx_api() -> None:
    fake = FakeWatsonx()
    assert narrator(fake).generate("the prompt") == TEXT

    iam_url, iam_headers, iam_body, _ = fake.calls[0]
    assert iam_url == "https://iam.cloud.ibm.com/identity/token"
    assert iam_headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert urllib.parse.parse_qs(iam_body.decode()) == {
        "grant_type": ["urn:ibm:params:oauth:grant-type:apikey"],
        "apikey": ["fake-key-for-tests"],
    }

    (url, headers, body, timeout_s) = fake.chat_calls()[0]
    assert url == "https://example.ml.cloud.ibm.com/ml/v1/text/chat?version=2024-10-08"
    assert headers["Authorization"] == "Bearer token-1"
    assert json.loads(body) == {
        "model_id": "ibm/granite-example",
        "project_id": "project-123",
        "messages": [{"role": "user", "content": "the prompt"}],
        "max_tokens": 300,
        "temperature": 0,
    }
    assert timeout_s == 20.0


def test_token_is_reused_until_close_to_expiry() -> None:
    fake, clock = FakeWatsonx(expires_in=3600), Clock()
    n = narrator(fake, clock)
    n.generate("a")
    clock.now = 3000
    n.generate("b")
    assert [c[0] for c in fake.calls].count(IAM_URL) == 1
    clock.now = 3541  # within the 60 s safety margin
    n.generate("c")
    assert [c[0] for c in fake.calls].count(IAM_URL) == 2
    assert fake.chat_calls()[-1][1]["Authorization"] == "Bearer token-2"


@pytest.mark.parametrize("reply", [{}, {"choices": []}, {"results": [{"generated_text": "x"}]}])
def test_unexpected_chat_response_raises(reply: Any) -> None:
    with pytest.raises(WatsonxError, match="unexpected chat response"):
        narrator(FakeWatsonx(reply=reply)).generate("p")


def test_http_errors_become_watsonx_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(
            request.full_url,
            404,
            "Not Found",
            {},  # type: ignore[arg-type]
            io.BytesIO(b'{"errors":[{"code":"model_not_supported"}]}'),
        )

    monkeypatch.setattr("sentinel.narrative.watsonx.urllib.request.urlopen", refuse)
    with pytest.raises(WatsonxError, match=r"HTTP 404 from example\.ml\.cloud\.ibm\.com") as exc:
        http_post("https://example.ml.cloud.ibm.com/ml/v1/text/chat", {}, b"{}", 1.0)
    assert "model_not_supported" in str(exc.value)


def test_explain_uses_granite_text_when_it_passes_the_check() -> None:
    e = explain(mock_alert(), narrator(FakeWatsonx()))
    assert e.source == "llm" and e.text == TEXT
    assert e.generator == "watsonx:ibm/granite-example" and e.language == "en"


def test_cli_template_and_alert_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source"] == "template" and out["language"] == "en"

    path = tmp_path / "alert.json"
    path.write_text(mock_alert(region="R7").model_dump_json(), encoding="utf-8")
    assert cli_main(["--alert", str(path), "--language", "pt"]) == 0
    assert "região R7" in json.loads(capsys.readouterr().out)["text"]


def test_cli_watsonx_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli_main(["--watsonx"]) == 2
    assert "WATSONX_APIKEY" in capsys.readouterr().err

    fake = FakeWatsonx()
    monkeypatch.setattr(WatsonxNarrator, "from_env", classmethod(lambda cls: narrator(fake)))
    assert cli_main(["--watsonx"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "llm"

    fake.reply = {"choices": [{"message": {"content": "[SIMULATION] Sensor P99 failed."}}]}
    assert cli_main(["--watsonx"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["fallback_reason"] == "narrative_rejected"


def test_explain_falls_back_when_watsonx_fails() -> None:
    def down(url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> Any:
        raise WatsonxError("request to example.ml.cloud.ibm.com failed: timed out")

    alert = mock_alert()
    n = WatsonxNarrator(WatsonxConfig.from_env(ENV), post=down)
    e = explain(alert, n)
    assert e.source == "template" and e.text == render_text(alert)
    assert e.fallback_reason is not None and "timed out" in e.fallback_reason
    assert "fake-key-for-tests" not in e.fallback_reason
