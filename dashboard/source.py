"""The dashboard's only data seam: an ``AlertSource`` returning parsed contract models.

Adapters: ``HttpAlertSource`` (a running backend) and ``PlaybackAlertSource`` (a recorded
run on disk, always shown with a "RECORDED REPLAY" banner). Every response body is parsed with the
contract models, so a backend that drifts from the contract raises ``ContractMismatchError``
instead of reaching the screen.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import AlertPage, HealthResponse, StatusResponse

MAX_LIMIT = 500


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

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_LIMIT or self.offset < 0:
            raise ValueError(f"limit must be 1..{MAX_LIMIT} and offset >= 0 (as the API)")


@dataclass(frozen=True)
class Recording:
    """Identifies a recorded run; ``None`` on a source means live data."""

    run_id: str
    recorded_at: str
    note: str


class AlertSource(Protocol):
    name: str
    recording: Recording | None

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
        self.recording: Recording | None = None

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


class PlaybackAlertSource:
    """Serves a recorded run from a directory: ``alerts.jsonl`` (one ``Alert`` per line, **in
    the order the backend stored them**: oldest first), ``status.json`` and, optionally,
    ``run.json`` (run id, date, note; without it the run is named after the directory).
    Same list semantics as ``GET /v1/alerts``. Files are re-read on every call."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.name = f"playback {run_dir}"
        self.recording: Recording | None = self._recording()

    def _read(self, name: str) -> bytes:
        try:
            return (self.run_dir / name).read_bytes()
        except OSError as exc:
            raise SourceError(f"{self.run_dir}/{name}: {exc.strerror or exc}") from exc

    def _recording(self) -> Recording:
        if not (self.run_dir / "run.json").exists():
            if not self.run_dir.is_dir():
                raise SourceError(f"{self.run_dir}: no such run directory (run.json, status.json)")
            return Recording(run_id=self.run_dir.name, recorded_at="unknown date", note="")
        try:
            meta = json.loads(self._read("run.json"))
            return Recording(
                run_id=str(meta["run_id"]),
                recorded_at=str(meta["recorded_at"]),
                note=str(meta.get("note", "")),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceError(f"{self.run_dir}/run.json: {exc}") from exc

    def _alerts(self) -> list[Alert]:
        try:
            text = self._read("alerts.jsonl").decode()
        except UnicodeDecodeError as exc:
            raise ContractMismatchError(f"alerts.jsonl: not UTF-8: {exc}") from exc
        lines = [line for line in text.splitlines() if line.strip()]
        return [parse(Alert, line.encode(), f"alerts.jsonl:{n}") for n, line in enumerate(lines, 1)]

    def health(self) -> HealthResponse:
        """The recording is readable (no live backend is involved)."""
        return HealthResponse(status="ok", version=self.status().version)

    def status(self) -> StatusResponse:
        return parse(StatusResponse, self._read("status.json"), "status.json")

    def get_alert(self, alert_id: str) -> Alert:
        for alert in self._alerts():
            if alert.alert_id == alert_id:
                return alert
        raise AlertNotFoundError(f"alert {alert_id} not in run {self.run_dir.name}")

    def list_alerts(self, query: AlertQuery) -> AlertPage:
        selected = [
            a
            for a in reversed(self._alerts())
            if (query.since is None or a.window_end >= query.since)
            and (query.until is None or a.window_end <= query.until)
            and (query.region is None or a.region == query.region)
        ]
        return AlertPage(
            items=tuple(selected[query.offset : query.offset + query.limit]),
            total=len(selected),
            limit=query.limit,
            offset=query.offset,
        )


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read())["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return exc.reason or ""
