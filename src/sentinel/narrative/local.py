"""Granite running on the LinuxONE itself (GGUF through llama.cpp): no cloud, no API key.

The model file found on the team VM, ``granite-4.0-h-350m-base-be.f16.gguf``, is a big-endian
(``-be``, required on s390x) 16-bit GGUF of Granite 4.0 H 350M **base**: a small hybrid model
that is not instruction-tuned. So the prompt here is few-shot completion (worked examples,
then the alert), not a chat with rules. Whatever it writes still goes through
``check_narrative`` and falls back to the template when it invents anything.

Two ways to run it:

- ``LlamaCppNarrator``: loads the GGUF in this Python process with llama-cpp-python, which the
  team VM's Jupyter kernel already has. No server.
- ``LocalGraniteNarrator``: talks to an OpenAI-compatible completions endpoint started
  separately (llama.cpp's ``llama-server`` or ``python -m llama_cpp.server``). Standard library
  only. Configuration (environment):

    LOCAL_LLM_URL        default http://127.0.0.1:8080
    LOCAL_LLM_MODEL      name reported in Explanation.generator, default granite-4.0-h-350m-base
    LOCAL_LLM_TIMEOUT_S  default 60 (CPU inference on a shared 3-vCPU VM)
"""

import importlib
import json
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sentinel.narrative.facts import DEFAULT_LANGUAGE, Language, alert_facts
from sentinel.narrative.watsonx import Post, WatsonxError
from sentinel.narrative.watsonx import http_post as _watsonx_post
from sentinel.schemas.alerts import Alert

DEFAULT_URL = "http://127.0.0.1:8080"
DEFAULT_MODEL = "granite-4.0-h-350m-base"
DEFAULT_MODEL_PATH = Path("~/granite-4.0-h-350m-base-be.f16.gguf")
STOP = ("\n\n", "\nRecord:", "\nRegistro:")


class LocalLLMError(Exception):
    """The local model server could not be reached or answered something unexpected."""


def http_post(url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> Any:
    try:
        return _watsonx_post(url, headers, body, timeout_s)
    except WatsonxError as exc:
        raise LocalLLMError(str(exc)) from exc


# Invented records: they only teach the model the shape of the answer. Only records with the
# same shape as the alert are shown: on the VM the model copied the region "R2" of an example
# into an alert without a region. The simulation notice is added by ``explain``, not asked of
# the model. If the model still copies an example's IDs or numbers, check_narrative rejects it.
Example = tuple[dict[str, Any], dict[Language, str]]

_REGIONAL: list[Example] = [
    (
        {
            "alert_id": "A-20180105T0300Z-1",
            "window_end": "2018-01-05T03:00:00Z",
            "region": "R2",
            "suspected_onset": "2018-01-04T23:40:00Z",
            "hours_since_onset": 3.33,
            "cusum": {"statistic": 9.7, "threshold": 8.0},
            "deviating_sensors": [
                {"sensor": "P05", "deviation_m": -0.74, "z": 3.9},
                {"sensor": "P11", "deviation_m": -0.52, "z": 3.1},
            ],
        },
        {
            "en": "Alert A-20180105T0300Z-1 reports a suspected leak in region R2. "
            "Sensors P05 and P11 read 0.74 m and 0.52 m below the expected pressure since "
            "2018-01-04 23:40 UTC. The CUSUM statistic reached 9.7 against a threshold of 8.0.",
            "pt": "O alerta A-20180105T0300Z-1 indica suspeita de vazamento na região R2. "
            "Os sensores P05 e P11 estão 0,74 m e 0,52 m abaixo do esperado desde "
            "2018-01-04 23:40 UTC. O CUSUM chegou a 9,7 para um limiar de 8,0.",
        },
    ),
    (
        {
            "alert_id": "A-20180320T1745Z-2",
            "window_end": "2018-03-20T17:45:00Z",
            "region": "R7",
            "suspected_onset": "2018-03-20T09:05:00Z",
            "hours_since_onset": 8.67,
            "cusum": {"statistic": 15.2, "threshold": 10.0},
            "deviating_sensors": [{"sensor": "P30", "deviation_m": -1.08, "z": 4.6}],
        },
        {
            "en": "Alert A-20180320T1745Z-2 reports a suspected leak in region R7. "
            "Sensor P30 read 1.08 m below the expected pressure since 2018-03-20 09:05 UTC. "
            "The CUSUM statistic reached 15.2 against a threshold of 10.0.",
            "pt": "O alerta A-20180320T1745Z-2 indica suspeita de vazamento na região R7. "
            "O sensor P30 está 1,08 m abaixo do esperado desde 2018-03-20 09:05 UTC. "
            "O CUSUM chegou a 15,2 para um limiar de 10,0.",
        },
    ),
]
_UNLOCATED: list[Example] = [
    (
        {
            "alert_id": "A-20180212T1015Z-1",
            "window_end": "2018-02-12T10:15:00Z",
            "suspected_onset": "2018-02-12T10:15:00Z",
            "deviating_sensors": [
                {"sensor": "P27", "deviation_m": -1.31, "z": 5.2},
                {"sensor": "P26", "deviation_m": -1.12, "z": 4.4},
            ],
        },
        {
            "en": "Alert A-20180212T1015Z-1 reports a suspected leak; the region was not "
            "estimated. Sensors P27 and P26 read 1.31 m and 1.12 m below the expected pressure "
            "at 2018-02-12 10:15 UTC.",
            "pt": "O alerta A-20180212T1015Z-1 indica suspeita de vazamento; a região não foi "
            "estimada. Os sensores P27 e P26 estão 1,31 m e 1,12 m abaixo do esperado em "
            "2018-02-12 10:15 UTC.",
        },
    ),
    (
        {
            "alert_id": "A-20180409T0630Z-1",
            "window_end": "2018-04-09T06:30:00Z",
            "deviating_sensors": [
                {"sensor": "P18", "deviation_m": -0.95, "z": 3.8},
                {"sensor": "P19", "deviation_m": -0.88, "z": 3.5},
                {"sensor": "P33", "deviation_m": -0.61, "z": 3.1},
            ],
        },
        {
            "en": "Alert A-20180409T0630Z-1 reports a suspected leak; the region was not "
            "estimated. Sensors P18, P19 and P33 read 0.95 m, 0.88 m and 0.61 m below the "
            "expected pressure at 2018-04-09 06:30 UTC.",
            "pt": "O alerta A-20180409T0630Z-1 indica suspeita de vazamento; a região não foi "
            "estimada. Os sensores P18, P19 e P33 estão 0,95 m, 0,88 m e 0,61 m abaixo do "
            "esperado em 2018-04-09 06:30 UTC.",
        },
    ),
]
_HEADER: dict[Language, str] = {
    "en": "Alert records from a water network leak detector, each followed by a short "
    "explanation for the operator. The explanation only restates the record.",
    "pt": "Registros de alerta de um detector de vazamentos, cada um seguido de uma "
    "explicação curta para o operador. A explicação só repete o registro.",
}
_LABELS: dict[Language, tuple[str, str]] = {
    "en": ("Record", "Explanation"),
    "pt": ("Registro", "Explicação"),
}


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_empty(v) for k, v in value.items() if v not in (None, [], {})}
    if isinstance(value, list):
        return [_drop_empty(v) for v in value]
    return value


def prompt_facts(alert: Alert) -> dict[str, Any]:
    """What the small model sees: the core of the alert, rounded to 2 decimals, without empty
    fields. Contributions, the detector summary, model names, the audit seq and ``is_mock``
    stay out: on the VM the 350M model passed contributions off as deviations, echoed the
    summary and ignored the simulation rule (``explain`` adds that notice itself). They remain
    in the evidence table and in the template."""
    facts = alert_facts(alert)
    core: dict[str, Any] = {
        key: facts[key] for key in ("alert_id", "window_end", "region", "suspected_onset")
    }
    core["hours_since_onset"] = facts["hours_since_onset"]
    if alert.cusum:
        core["cusum"] = {
            "statistic": round(alert.cusum.statistic, 2),
            "threshold": round(alert.cusum.threshold, 2),
        }
    core["deviating_sensors"] = [
        {
            "sensor": d.sensor,
            "deviation_m": round(d.deviation_m, 2),
            "z": round(d.z, 2) if d.z is not None else None,
            "since": facts["deviating_sensors"][i]["since"],
        }
        for i, d in enumerate(alert.deviating_sensors)
    ]
    slim: dict[str, Any] = _drop_empty(core)
    return slim


def build_completion_prompt(alert: Alert, language: Language = DEFAULT_LANGUAGE) -> str:
    record, explanation = _LABELS[language]
    parts = [_HEADER[language], ""]
    for facts, texts in _REGIONAL if alert.region else _UNLOCATED:
        parts += [
            f"{record}: {json.dumps(facts, ensure_ascii=False)}",
            f"{explanation}: {texts[language]}",
            "",
        ]
    parts += [
        f"{record}: {json.dumps(prompt_facts(alert), ensure_ascii=False)}",
        f"{explanation}:",
    ]
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class LocalLLMConfig:
    url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    timeout_s: float = 60.0
    max_tokens: int = 120

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LocalLLMConfig":
        e = os.environ if env is None else env
        try:
            timeout_s = float(e.get("LOCAL_LLM_TIMEOUT_S", "60"))
        except ValueError as exc:
            raise LocalLLMError("LOCAL_LLM_TIMEOUT_S must be a number") from exc
        return cls(
            url=e.get("LOCAL_LLM_URL", DEFAULT_URL).rstrip("/"),
            model=e.get("LOCAL_LLM_MODEL", DEFAULT_MODEL),
            timeout_s=timeout_s,
        )


class LocalGraniteNarrator:
    """``Narrator`` for a Granite GGUF served on the VM. ``explain`` asks it for its own
    prompt (``prompt_for``): a base model follows examples, not the rules of the chat
    prompt used with watsonx."""

    def __init__(self, config: LocalLLMConfig | None = None, *, post: Post = http_post) -> None:
        self.config = config or LocalLLMConfig()
        self._post = post

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LocalGraniteNarrator":
        return cls(LocalLLMConfig.from_env(env))

    @property
    def name(self) -> str:
        return f"local:{self.config.model}"

    def prompt_for(self, alert: Alert, language: Language) -> str:
        return build_completion_prompt(alert, language)

    def generate(self, prompt: str) -> str:
        body = {
            "model": self.config.model,
            "prompt": prompt,
            "max_tokens": self.config.max_tokens,
            "temperature": 0,
            "stop": list(STOP),
        }
        data = self._post(
            f"{self.config.url}/v1/completions",
            {"Content-Type": "application/json", "Accept": "application/json"},
            json.dumps(body).encode("utf-8"),
            self.config.timeout_s,
        )
        return _completion_text(data)


class LlamaCppNarrator:
    """Same few-shot prompt, with the GGUF loaded in this Python process by llama-cpp-python
    (``llama_cpp``, already installed in the team VM's Jupyter kernel): no server to start.

    The model is loaded on the first explanation and kept; ``close()`` frees the memory (the
    VM is shared). Calls are serialised: a llama.cpp context is not thread-safe. Environment:

        LOCAL_LLM_MODEL_PATH  default ~/granite-4.0-h-350m-base-be.f16.gguf
        LOCAL_LLM_THREADS     default 1 (3 vCPUs for the whole team)
        LOCAL_LLM_CTX         default 2048 tokens (prompt + answer)
    """

    def __init__(
        self,
        model_path: Path | str = DEFAULT_MODEL_PATH,
        *,
        n_threads: int = 1,
        n_ctx: int = 2048,
        max_tokens: int = 120,
        llama_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_path = Path(model_path).expanduser()
        self.n_threads = n_threads
        self.n_ctx = n_ctx
        self.max_tokens = max_tokens
        self._factory = llama_factory
        self._llm: Any = None
        self._lock = threading.Lock()
        # Token counts of the last completion (prompt_tokens, completion_tokens), when the
        # runtime reports them: they explain where the generation time goes.
        self.last_usage: dict[str, Any] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LlamaCppNarrator":
        e = os.environ if env is None else env
        path = Path(e.get("LOCAL_LLM_MODEL_PATH", str(DEFAULT_MODEL_PATH))).expanduser()
        if not path.is_file():
            raise LocalLLMError(f"model file not found: {path} (set LOCAL_LLM_MODEL_PATH)")
        try:
            threads = int(e.get("LOCAL_LLM_THREADS", "1"))
            ctx = int(e.get("LOCAL_LLM_CTX", "2048"))
        except ValueError as exc:
            raise LocalLLMError("LOCAL_LLM_THREADS and LOCAL_LLM_CTX must be integers") from exc
        return cls(path, n_threads=threads, n_ctx=ctx)

    @property
    def name(self) -> str:
        return f"llamacpp:{self.model_path.name}"

    @property
    def loaded(self) -> bool:
        return self._llm is not None

    def prompt_for(self, alert: Alert, language: Language) -> str:
        return build_completion_prompt(alert, language)

    def load(self) -> Any:
        """Load the model now (otherwise the first explanation does it)."""
        with self._lock:
            return self._load()

    def generate(self, prompt: str) -> str:
        with self._lock:
            out = self._load().create_completion(
                prompt, max_tokens=self.max_tokens, temperature=0.0, stop=list(STOP)
            )
            usage = out.get("usage") if isinstance(out, dict) else None
            self.last_usage = dict(usage) if isinstance(usage, dict) else None
        return _completion_text(out)

    def close(self) -> None:
        with self._lock:
            llm, self._llm = self._llm, None
        if llm is not None and hasattr(llm, "close"):
            llm.close()

    def _load(self) -> Any:
        if self._llm is None:
            factory = self._factory
            if factory is None:
                try:  # optional dependency, present on the team VM only
                    factory = importlib.import_module("llama_cpp").Llama
                except ImportError as exc:
                    raise LocalLLMError("llama_cpp (llama-cpp-python) is not installed") from exc
            try:
                self._llm = factory(
                    model_path=str(self.model_path),
                    n_ctx=self.n_ctx,
                    n_threads=self.n_threads,
                    seed=0,
                    verbose=False,
                )
            except Exception as exc:  # llama.cpp raises ValueError/RuntimeError on bad files
                raise LocalLLMError(f"could not load {self.model_path}: {exc}") from exc
        return self._llm


def _completion_text(data: Any) -> str:
    try:
        text = str(data["choices"][0]["text"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise LocalLLMError("unexpected completion response: no choices[0].text") from exc
    # Cut a sentence left unfinished by max_tokens.
    if "." in text and not text.endswith("."):
        text = text[: text.rfind(".") + 1]
    return text
