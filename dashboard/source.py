"""The dashboard's only data seam: an ``AlertSource`` returning parsed contract models.

Adapters: ``HttpAlertSource`` (a running backend). Every response body is parsed with the
contract models, so a backend that drifts from the contract raises ``ContractMismatchError``
instead of reaching the screen.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ValidationError

from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import AlertPage, HealthResponse, StatusResponse


class SourceError(Exception):
    """Base class: the source could not produce a contract-valid answer."""


class SourceUnavailableError(SourceError):
    """The backend did not answer (connection refused, timeout, 5xx)."""


class ContractMismatchError(SourceError):
    """The answer does not match the contract models."""


class AlertNotFoundError(SourceError):
    """No alert with that ID."""


@dataclass(frozen=True)
class AlertQuery:
    """Same parameters and semantics as ``GET /v1/alerts`` (contract §5)."""

    limit: int = 50
    offset: int = 0
    since: datetime | None = None
    until: datetime | None = None
    region: str | None = None


class AlertSource(Protocol):
    name: str

    def health(self) -> HealthResponse: ...
    def status(self) -> StatusResponse: ...
    def list_alerts(self, query: AlertQuery) -> AlertPage: ...
    def get_alert(self, alert_id: str) -> Alert: ...


def parse[M: BaseModel](model: type[M], body: bytes, what: str) -> M:
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise ContractMismatchError(
            f"{what}: {exc.error_count()} contract error(s): {exc}"
        ) from exc


class HttpAlertSource:
    """Reads a backend over HTTP (stdlib only)."""

    def __init__(self, base_url: str, timeout_s: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.name = f"http {self.base_url}"

    def _get(self, path: str, params: dict[str, str] | None = None) -> bytes:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                body: bytes = resp.read()
                return body
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise AlertNotFoundError(_error_message(exc)) from exc
            if exc.code >= 500:
                raise SourceUnavailableError(f"{path}: HTTP {exc.code}") from exc
            raise SourceError(f"{path}: HTTP {exc.code}: {_error_message(exc)}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise SourceUnavailableError(f"{path}: {exc}") from exc

    def health(self) -> HealthResponse:
        return parse(HealthResponse, self._get("/health"), "/health")

    def status(self) -> StatusResponse:
        return parse(StatusResponse, self._get("/v1/status"), "/v1/status")

    def list_alerts(self, query: AlertQuery) -> AlertPage:
        params = {"limit": str(query.limit), "offset": str(query.offset)}
        if query.since is not None:
            params["since"] = query.since.isoformat()
        if query.until is not None:
            params["until"] = query.until.isoformat()
        if query.region is not None:
            params["region"] = query.region
        return parse(AlertPage, self._get("/v1/alerts", params), "/v1/alerts")

    def get_alert(self, alert_id: str) -> Alert:
        path = f"/v1/alerts/{urllib.parse.quote(alert_id, safe='')}"
        return parse(Alert, self._get(path), path)


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read())["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return exc.reason or ""
