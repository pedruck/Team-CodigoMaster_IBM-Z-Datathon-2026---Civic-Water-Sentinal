"""The dashboard's only data seam: an ``AlertSource`` returning parsed contract models.

Adapters: ``HttpAlertSource`` (a running backend, optionally behind the read-only gateway with
a bearer token), ``SnapshotAlertSource`` (a JSON snapshot the VM pushes to external storage),
``PlaybackAlertSource`` (a recorded run on disk, always shown with a "RECORDED REPLAY" banner)
and ``FallbackAlertSource`` (a live source that falls back to a recording while it is down).
A source may carry a ``notice`` the page shows next to the mandatory banners (BE-026).
Every response body is parsed with the contract models, so a backend that drifts from the
contract raises ``ContractMismatchError`` instead of reaching the screen.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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
    # Read-only members: an adapter may compute them (FallbackAlertSource does).
    @property
    def name(self) -> str: ...
    @property
    def recording(self) -> Recording | None: ...

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
    """Reads a backend over HTTP (stdlib only). ``token`` is sent as a bearer token, for the
    read-only gateway in front of the API on the VM (``scripts/readonly_gateway.py``)."""

    def __init__(self, base_url: str, timeout_s: float = 5.0, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.name = f"http {self.base_url}"
        self.recording: Recording | None = None
        self.notice: str | None = None
        self.live_label: str | None = None  # a live API: the page says CONNECTED or OFFLINE
        self._headers = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, params: dict[str, str] | None = None) -> bytes:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                body: bytes = resp.read()
                return body
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise AlertNotFoundError(_error_message(exc)) from exc
            if exc.code == 401:
                raise SourceError(f"{path}: HTTP 401: the gateway refused the token") from exc
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
        self.notice: str | None = None

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
        return page_of(list(reversed(self._alerts())), query)


def page_of(newest_first: list[Alert], query: AlertQuery) -> AlertPage:
    """``GET /v1/alerts`` semantics over alerts held in memory, newest first."""
    selected = [
        a
        for a in newest_first
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


SNAPSHOT_SCHEMA = "civicwater.dashboard-snapshot/1"


@dataclass(frozen=True)
class Snapshot:
    taken_at: datetime
    health: HealthResponse
    status: StatusResponse
    alerts: tuple[Alert, ...]  # newest first, as the API listed them


def parse_snapshot(body: bytes, what: str) -> Snapshot:
    """The document ``scripts/push_snapshot.py`` writes: the API's own answers, validated here
    with the contract models like any other response."""
    try:
        doc = json.loads(body)
    except ValueError as exc:
        raise ContractMismatchError(f"{what}: not JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SNAPSHOT_SCHEMA:
        raise ContractMismatchError(f"{what}: not a {SNAPSHOT_SCHEMA} document")
    try:
        taken_at = datetime.strptime(str(doc["taken_at"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        parts = (doc["health"], doc["status"], doc["alerts"])
    except (KeyError, ValueError) as exc:
        raise ContractMismatchError(f"{what}: {exc}") from exc
    health_doc, status_doc, alerts_doc = parts
    health = parse(HealthResponse, json.dumps(health_doc).encode(), f"{what}:health")
    status = parse(StatusResponse, json.dumps(status_doc).encode(), f"{what}:status")
    page = parse(AlertPage, json.dumps(alerts_doc).encode(), f"{what}:alerts")
    return Snapshot(taken_at, health, status, page.items)


class SnapshotAlertSource:
    """Reads the snapshot the VM pushes to external storage (option 3 of the external-dashboard
    plan): an ``https://`` URL (e.g. a pre-signed GET URL) or a local file. Near-live: the
    ``notice`` says when the snapshot was taken and warns once it is older than ``max_age_s``.
    The document is fetched at most once per ``cache_s`` seconds."""

    def __init__(
        self,
        location: str,
        *,
        timeout_s: float = 10.0,
        max_age_s: float = 300.0,
        cache_s: float = 5.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.location = location
        self.timeout_s = timeout_s
        self.max_age_s = max_age_s
        self.cache_s = cache_s
        self._now = now
        self._clock = clock
        self._cached: tuple[float, Snapshot] | None = None
        self.name = f"snapshot {_without_query(location)}"
        self.recording: Recording | None = None
        self.notice: str | None = None
        self.live_label: str | None = "SNAPSHOT"

    def _read(self) -> bytes:
        if not self.location.startswith(("https://", "http://")):
            try:
                return Path(self.location).read_bytes()
            except OSError as exc:
                raise SourceUnavailableError(f"{self.location}: {exc.strerror or exc}") from exc
        try:
            with urllib.request.urlopen(self.location, timeout=self.timeout_s) as resp:
                body: bytes = resp.read()
                return body
        except urllib.error.HTTPError as exc:
            # 403/404 mean the URL expired or nothing was pushed yet: the source is not there.
            raise SourceUnavailableError(f"{self.name}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise SourceUnavailableError(f"{self.name}: {exc}") from exc

    def snapshot(self) -> Snapshot:
        if self._cached is not None and self._clock() - self._cached[0] < self.cache_s:
            return self._cached[1]
        snap = parse_snapshot(self._read(), self.name)
        self._cached = (self._clock(), snap)
        age = max(0.0, (self._now() - snap.taken_at).total_seconds())
        when = snap.taken_at.strftime("%Y-%m-%d %H:%M UTC")
        if age > self.max_age_s:
            self.notice = (
                f"SNAPSHOT IS {_minutes(age)} OLD — last copy taken {when}; the VM may not be "
                "sending updates."
            )
        else:
            self.notice = f"SNAPSHOT — copy of the API taken {when} ({_minutes(age)} ago)."
        return snap

    def health(self) -> HealthResponse:
        return self.snapshot().health

    def status(self) -> StatusResponse:
        return self.snapshot().status

    def list_alerts(self, query: AlertQuery) -> AlertPage:
        return page_of(list(self.snapshot().alerts), query)

    def get_alert(self, alert_id: str) -> Alert:
        for alert in self.snapshot().alerts:
            if alert.alert_id == alert_id:
                return alert
        raise AlertNotFoundError(f"alert {alert_id} not in the snapshot")


class FallbackAlertSource:
    """A live source that shows a recorded run while the live one does not answer, and goes
    back to it on the next successful health check. The recording keeps its RECORDED REPLAY
    banner; ``notice`` says why it is on screen."""

    def __init__(self, live: AlertSource, fallback: PlaybackAlertSource) -> None:
        self.live = live
        self.fallback = fallback
        self.using_fallback = False
        self.reason = ""

    @property
    def _active(self) -> AlertSource:
        return self.fallback if self.using_fallback else self.live

    @property
    def name(self) -> str:
        return self._active.name

    @property
    def recording(self) -> Recording | None:
        return self.fallback.recording if self.using_fallback else self.live.recording

    @property
    def notice(self) -> str | None:
        if self.using_fallback:
            # Short on the page; the reason itself is in the sidebar's "Source & models".
            return "LIVE SOURCE UNAVAILABLE — showing the recorded run until it answers again."
        return getattr(self.live, "notice", None)

    @property
    def live_label(self) -> str | None:
        return None if self.using_fallback else getattr(self.live, "live_label", None)

    def health(self) -> HealthResponse:
        try:
            answer = self.live.health()
        except SourceUnavailableError as exc:
            self.using_fallback, self.reason = True, str(exc)
            return self.fallback.health()
        self.using_fallback, self.reason = False, ""
        return answer

    def status(self) -> StatusResponse:
        return self._active.status()

    def list_alerts(self, query: AlertQuery) -> AlertPage:
        return self._active.list_alerts(query)

    def get_alert(self, alert_id: str) -> Alert:
        return self._active.get_alert(alert_id)


def _without_query(location: str) -> str:
    """A pre-signed URL carries its signature in the query: never show or log it."""
    parts = urllib.parse.urlsplit(location)
    return f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme else location


def _minutes(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "less than a minute"
    if minutes < 120:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read())["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return exc.reason or ""
