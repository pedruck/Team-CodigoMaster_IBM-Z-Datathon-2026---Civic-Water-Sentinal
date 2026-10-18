"""watsonx.ai (Granite) narrator over the REST chat API. Narrative owner (P5).

Standard library only: nothing extra to install on LinuxONE. Two calls:

- IAM token: ``POST https://iam.cloud.ibm.com/identity/token`` (form: apikey grant), cached
  until shortly before ``expires_in``;
- chat: ``POST {WATSONX_URL}/ml/v1/text/chat?version=...`` with ``model_id``, ``project_id``
  and ``messages``; the text is ``choices[0].message.content``.

Configuration comes from the environment and is never committed:

    WATSONX_APIKEY       IBM Cloud API key (secret)
    WATSONX_URL          regional endpoint, e.g. https://us-south.ml.cloud.ibm.com
    WATSONX_PROJECT_ID   watsonx.ai project ID
    WATSONX_MODEL_ID     a Granite chat model available in that project
    WATSONX_API_VERSION  optional, default DEFAULT_API_VERSION
    WATSONX_TIMEOUT_S    optional, default 20

Any failure raises; ``explain`` then falls back to the deterministic template.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

IAM_URL = "https://iam.cloud.ibm.com/identity/token"
DEFAULT_API_VERSION = "2024-10-08"
TOKEN_MARGIN_S = 60.0
_REQUIRED = ("WATSONX_APIKEY", "WATSONX_URL", "WATSONX_PROJECT_ID", "WATSONX_MODEL_ID")

Post = Callable[[str, Mapping[str, str], bytes, float], Any]
"""``post(url, headers, body, timeout_s) -> parsed JSON``; injectable for tests."""


class WatsonxError(Exception):
    """watsonx.ai could not be reached or answered something unexpected."""


def http_post(url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> Any:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode("utf-8", "replace")
        host = urllib.parse.urlsplit(url).netloc
        raise WatsonxError(f"HTTP {exc.code} from {host}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        host = urllib.parse.urlsplit(url).netloc
        raise WatsonxError(f"request to {host} failed: {exc}") from exc


@dataclass(frozen=True, slots=True)
class WatsonxConfig:
    api_key: str = field(repr=False)
    url: str
    project_id: str
    model_id: str
    api_version: str = DEFAULT_API_VERSION
    timeout_s: float = 20.0
    max_tokens: int = 300

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WatsonxConfig":
        e = os.environ if env is None else env
        missing = [k for k in _REQUIRED if not e.get(k)]
        if missing:
            raise WatsonxError(f"missing environment variables: {', '.join(missing)}")
        try:
            timeout_s = float(e.get("WATSONX_TIMEOUT_S", "20"))
        except ValueError as exc:
            raise WatsonxError("WATSONX_TIMEOUT_S must be a number") from exc
        return cls(
            api_key=e["WATSONX_APIKEY"],
            url=e["WATSONX_URL"].rstrip("/"),
            project_id=e["WATSONX_PROJECT_ID"],
            model_id=e["WATSONX_MODEL_ID"],
            api_version=e.get("WATSONX_API_VERSION", DEFAULT_API_VERSION),
            timeout_s=timeout_s,
        )


class WatsonxNarrator:
    """``Narrator`` backed by a Granite model on watsonx.ai. Temperature 0 so the same alert
    gives the same text as far as the service allows."""

    def __init__(
        self,
        config: WatsonxConfig,
        *,
        post: Post = http_post,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._post = post
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._token_valid_until = 0.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WatsonxNarrator":
        return cls(WatsonxConfig.from_env(env))

    @property
    def name(self) -> str:
        return f"watsonx:{self.config.model_id}"

    def generate(self, prompt: str) -> str:
        body = {
            "model_id": self.config.model_id,
            "project_id": self.config.project_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.config.max_tokens,
            "temperature": 0,
        }
        data = self._post(
            f"{self.config.url}/ml/v1/text/chat?version={self.config.api_version}",
            {
                "Authorization": f"Bearer {self._bearer()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json.dumps(body).encode("utf-8"),
            self.config.timeout_s,
        )
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise WatsonxError("unexpected chat response: no choices[0].message.content") from exc

    def _bearer(self) -> str:
        with self._lock:
            now = self._clock()
            if self._token is None or now >= self._token_valid_until:
                form = urllib.parse.urlencode(
                    {
                        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                        "apikey": self.config.api_key,
                    }
                )
                data = self._post(
                    IAM_URL,
                    {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    form.encode("ascii"),
                    self.config.timeout_s,
                )
                try:
                    token, expires_in = str(data["access_token"]), float(data["expires_in"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise WatsonxError("unexpected IAM response: no access_token") from exc
                self._token = token
                self._token_valid_until = now + max(expires_in - TOKEN_MARGIN_S, 0.0)
            return self._token
