"""Dashboard hosted off the VM (BE-026): the read-only gateway, the snapshot push, the pre-signed
URL, and the dashboard sources that read them. Upstream APIs and storage are local fakes; the
alerts come from the committed synthetic playback."""

import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from dashboard.config import source_from_env
from dashboard.source import (
    AlertQuery,
    ContractMismatchError,
    FallbackAlertSource,
    HttpAlertSource,
    PlaybackAlertSource,
    SnapshotAlertSource,
    SourceError,
    SourceUnavailableError,
    parse_snapshot,
)

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "dashboard" / "playback" / "fixture-synthetic-ltown-demo"
TOKEN = "t" * 32


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway_mod = load_script("readonly_gateway")
snapshot_mod = load_script("push_snapshot")
presign_mod = load_script("presign_url")


def api_answers() -> dict[str, bytes]:
    """What a backend would answer, from the committed demo recording."""
    source = PlaybackAlertSource(DEMO)
    page = source.list_alerts(AlertQuery(limit=500))
    return {
        "/health": source.health().model_dump_json().encode(),
        "/v1/status": source.status().model_dump_json().encode(),
        "/v1/alerts": page.model_dump_json().encode(),
        f"/v1/alerts/{page.items[0].alert_id}": page.items[0].model_dump_json().encode(),
    }


class FakeServer:
    """A local HTTP server; records every request it receives."""

    def __init__(self, routes: dict[str, bytes], *, accept_put: bool = False) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        self.stored: bytes | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                outer.requests.append(("GET", self.path, dict(self.headers), b""))
                body = outer.routes.get(self.path.split("?")[0])
                self._reply(200, body) if body is not None else self._reply(404, b"{}")

            def do_PUT(self) -> None:
                size = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(size)
                outer.requests.append(("PUT", self.path, dict(self.headers), body))
                if accept_put:
                    outer.stored = body
                self._reply(200 if accept_put else 403, b"")

            do_POST = do_PUT  # noqa: N815 (http.server dispatch name)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def api() -> Iterator[FakeServer]:
    server = FakeServer(api_answers())
    yield server
    server.close()


@pytest.fixture
def gateway(api: FakeServer) -> Iterator[tuple[str, list[str]]]:
    log: list[str] = []
    gw = gateway_mod.Gateway(
        api.url, TOKEN, timeout_s=5, limiter=gateway_mod.RateLimiter(1000, burst=1000)
    )
    handler = gateway_mod.make_handler(gw, log.append)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", log
    server.shutdown()
    server.server_close()


def call(url: str, *, token: str | None = TOKEN, method: str = "GET") -> tuple[int, bytes]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(
        url, headers=headers, method=method, data=b"{}" if method == "POST" else None
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


# ------------------------------------------------------------------ read-only gateway


def test_gateway_token_file_must_be_private_and_long(tmp_path: Path) -> None:
    path = tmp_path / "gateway.token"
    path.write_text(TOKEN + "\n")
    os.chmod(path, 0o644)
    with pytest.raises(gateway_mod.GatewayError, match="chmod 600"):
        gateway_mod.read_token(path)
    os.chmod(path, 0o600)
    assert gateway_mod.read_token(path) == TOKEN
    path.write_text("short")
    with pytest.raises(gateway_mod.GatewayError, match="at least"):
        gateway_mod.read_token(path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/health", "/health"),
        ("/v1/status", "/v1/status"),
        ("/v1/alerts?limit=50&region=Z1-R3", "/v1/alerts?limit=50&region=Z1-R3"),
        ("/v1/alerts/A-20190314T1705Z-1", "/v1/alerts/A-20190314T1705Z-1"),
        ("/v1/alerts/A-1/explanation", "/v1/alerts/A-1/explanation"),
        ("/docs", None),
        ("/openapi.json", None),
        ("/v1/windows", None),
        ("/v1/audit", None),
        ("/v1/audit/head", None),
        ("/v1/alerts?limit=5&format=csv", None),
        ("/v1/status?x=1", None),
        ("/v1/alerts/../windows", None),
        ("/v1/alerts/a%2Fb", None),
        ("/v1/alerts?region=" + "x" * 100, None),
    ],
)
def test_gateway_allows_only_the_read_routes(raw: str, expected: str | None) -> None:
    assert gateway_mod.allowed_target(raw) == expected


def test_gateway_forwards_reads_with_the_token(
    api: FakeServer, gateway: tuple[str, list[str]]
) -> None:
    url, log = gateway
    status, body = call(f"{url}/v1/status")

    assert status == 200
    assert json.loads(body)["mocks_active"] is True
    assert api.requests[-1][0:2] == ("GET", "/v1/status")
    assert "authorization" not in {k.lower() for k in api.requests[-1][2]}  # token stays here
    assert all(TOKEN not in line for line in log)


def test_gateway_refuses_without_token_writes_and_other_routes(
    api: FakeServer, gateway: tuple[str, list[str]]
) -> None:
    url, _ = gateway
    assert call(f"{url}/v1/status", token=None)[0] == 401
    assert call(f"{url}/v1/status", token="x" * 32)[0] == 401
    assert call(f"{url}/v1/windows", method="POST")[0] == 405
    assert call(f"{url}/docs")[0] == 404
    assert call(f"{url}/v1/audit")[0] == 404
    assert api.requests == []  # nothing reached the API


def test_gateway_reports_an_unreachable_api() -> None:
    gw = gateway_mod.Gateway(
        "http://127.0.0.1:9", TOKEN, timeout_s=1, limiter=gateway_mod.RateLimiter(10, burst=10)
    )
    status, body = gw.fetch("/health")
    assert status == 502
    assert json.loads(body)["error"]["code"] == "upstream_unavailable"


def test_gateway_rate_limit_per_client() -> None:
    now = [0.0]
    limiter = gateway_mod.RateLimiter(1.0, burst=2, clock=lambda: now[0])
    assert [limiter.allow("a") for _ in range(3)] == [True, True, False]
    assert limiter.allow("b")
    now[0] = 1.0
    assert limiter.allow("a")


def test_gateway_needs_a_plain_upstream() -> None:
    with pytest.raises(gateway_mod.GatewayError):
        gateway_mod.Gateway("http://h:8000/v1?x", TOKEN, timeout_s=1, limiter=None)


# ------------------------------------------------------------------ snapshot push


def test_snapshot_is_the_api_answers_with_a_timestamp(api: FakeServer) -> None:
    doc = snapshot_mod.take_snapshot(
        api.url, limit=9999, now=lambda: datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    )

    assert doc["schema"] == "civicwater.dashboard-snapshot/1"
    assert doc["taken_at"] == "2026-10-18T12:00:00Z"
    assert len(doc["alerts"]["items"]) == 6
    assert any("limit=500" in path for _, path, _, _ in api.requests)  # capped at the API max


def test_snapshot_put_and_loop(api: FakeServer) -> None:
    storage = FakeServer({}, accept_put=True)
    lines: list[str] = []
    try:
        failures = snapshot_mod.run(
            api.url,
            storage.url + "/b/snapshot.json?X-Amz-Signature=secret",
            every_s=60,
            count=3,
            limit=50,
            sleep=lambda _: None,
            log=lines.append,
        )
    finally:
        storage.close()

    assert failures == 0
    assert [r[0] for r in storage.requests] == ["PUT", "PUT", "PUT"]
    assert parse_snapshot(storage.stored or b"", "stored").status.mocks_active is True
    assert all("secret" not in line for line in lines)  # the signature is never printed


def test_snapshot_skips_upload_when_the_api_is_down() -> None:
    storage = FakeServer({}, accept_put=True)
    lines: list[str] = []
    try:
        failures = snapshot_mod.run(
            "http://127.0.0.1:9",
            storage.url,
            every_s=1,
            count=1,
            limit=5,
            sleep=lambda _: None,
            log=lines.append,
        )
    finally:
        storage.close()
    assert failures == 1
    assert storage.requests == []  # the last good snapshot stays
    assert "skipped" in lines[0]


def test_snapshot_url_comes_from_env_or_prompt_never_argv() -> None:
    url = "https://bucket.example/snapshot.json?X-Amz-Signature=s"
    assert snapshot_mod.put_url({"SNAPSHOT_PUT_URL": url}) == url
    assert snapshot_mod.put_url({}, prompt=lambda _: url) == url
    with pytest.raises(SystemExit):
        snapshot_mod.put_url({"SNAPSHOT_PUT_URL": "http://insecure/x"})
    with pytest.raises(SystemExit):
        snapshot_mod.main(["--api", "http://x", "--url", url])  # no such argument


# ------------------------------------------------------------------ pre-signed URL


def test_presign_matches_the_aws_sigv4_example() -> None:
    # AWS S3 documentation, "Example: presigned URL" (GET test.txt, us-east-1, 2013-05-24).
    url = presign_mod.presign(
        "GET",
        host="examplebucket.s3.amazonaws.com",
        path="/test.txt",
        region="us-east-1",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        when=datetime(2013, 5, 24, tzinfo=UTC),
        expires=86400,
    )
    assert url.endswith(
        "X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"
    )
    with pytest.raises(ValueError, match="expires"):
        presign_mod.presign(
            "PUT",
            host="h",
            path="/k",
            region="auto",
            access_key="a",
            secret_key="s",
            when=datetime(2026, 1, 1, tzinfo=UTC),
            expires=8 * 24 * 3600,
        )


# ------------------------------------------------------------------ dashboard sources


def snapshot_file(tmp_path: Path, taken_at: str = "2026-10-18T12:00:00Z") -> Path:
    answers = api_answers()
    doc = {
        "schema": "civicwater.dashboard-snapshot/1",
        "taken_at": taken_at,
        "health": json.loads(answers["/health"]),
        "status": json.loads(answers["/v1/status"]),
        "alerts": json.loads(answers["/v1/alerts"]),
    }
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(doc))
    return path


def test_snapshot_source_serves_the_api_semantics_and_says_its_age(tmp_path: Path) -> None:
    clock = [datetime(2026, 10, 18, 12, 3, tzinfo=UTC)]
    source = SnapshotAlertSource(str(snapshot_file(tmp_path)), now=lambda: clock[0], cache_s=0)

    assert source.status().mocks_active is True
    assert source.notice == "SNAPSHOT — copy of the API taken 2026-10-18 12:00 UTC (3 min ago)."
    page = source.list_alerts(AlertQuery(limit=2, region="Z1-R3"))
    assert page.total == 1 and page.items[0].region == "Z1-R3"
    assert source.get_alert(page.items[0].alert_id) == page.items[0]
    assert source.recording is None

    clock[0] += timedelta(hours=2)
    source.health()
    assert source.notice is not None and source.notice.startswith("SNAPSHOT IS 2 h 3 min OLD")


def test_snapshot_source_errors(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailableError):
        SnapshotAlertSource(str(tmp_path / "missing.json")).status()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "other"}))
    with pytest.raises(ContractMismatchError):
        SnapshotAlertSource(str(bad)).status()
    signed = SnapshotAlertSource("https://bucket.example/s.json?X-Amz-Signature=secret")
    assert "secret" not in signed.name


def test_http_source_sends_the_gateway_token(gateway: tuple[str, list[str]]) -> None:
    url, _ = gateway
    assert HttpAlertSource(url, token=TOKEN).status().mocks_active is True
    with pytest.raises(SourceError, match="refused the token"):
        HttpAlertSource(url, token="x" * 32).status()


def test_fallback_shows_the_recording_while_the_live_source_is_down(tmp_path: Path) -> None:
    live = SnapshotAlertSource(str(tmp_path / "not-yet.json"), cache_s=0)
    source = FallbackAlertSource(live, PlaybackAlertSource(DEMO))

    source.health()
    assert source.using_fallback
    assert (
        source.recording is not None and source.recording.run_id == "fixture-synthetic-ltown-demo"
    )
    assert source.notice is not None and source.notice.startswith("LIVE SOURCE UNAVAILABLE")
    assert source.list_alerts(AlertQuery()).total == 6

    live.location = str(snapshot_file(tmp_path))
    source.health()
    assert not source.using_fallback
    assert source.recording is None
    assert source.notice is not None and source.notice.startswith("SNAPSHOT")


def test_config_selects_the_source(tmp_path: Path) -> None:
    snap = str(snapshot_file(tmp_path))
    assert isinstance(
        source_from_env({"SENTINEL_DASHBOARD_SNAPSHOT_URL": snap}), SnapshotAlertSource
    )
    http = source_from_env({"SENTINEL_DASHBOARD_API_TOKEN": TOKEN})
    assert isinstance(http, HttpAlertSource)
    wrapped = source_from_env(
        {"SENTINEL_DASHBOARD_SNAPSHOT_URL": snap, "SENTINEL_DASHBOARD_FALLBACK_DIR": str(DEMO)}
    )
    assert isinstance(wrapped, FallbackAlertSource)
    played = source_from_env(
        {"SENTINEL_DASHBOARD_PLAYBACK_DIR": str(DEMO), "SENTINEL_DASHBOARD_SNAPSHOT_URL": snap}
    )
    assert isinstance(played, PlaybackAlertSource)  # an explicit recording wins


@pytest.mark.parametrize("page", ["../dashboard/app.py", "../dashboard/pages/network_map_page.py"])
def test_pages_show_the_snapshot_age(
    page: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.delenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", raising=False)
    monkeypatch.setenv("SENTINEL_DASHBOARD_SNAPSHOT_URL", str(snapshot_file(tmp_path)))

    app: Any = testing.AppTest.from_file(page).run()

    assert not app.exception
    assert any(
        "copy of the API taken 2026-10-18 12:00 UTC" in w.value or "SNAPSHOT IS" in w.value
        for w in app.warning
    )
    assert not any("RECORDED REPLAY" in w.value for w in app.warning)
    if page.endswith("app.py"):
        html = "".join(e.proto.body for e in app.get("html"))
        assert "SNAPSHOT</span>" in html and "CONNECTED" not in html


def test_app_falls_back_to_the_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.delenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", raising=False)
    monkeypatch.setenv("SENTINEL_DASHBOARD_API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SENTINEL_DASHBOARD_TIMEOUT_S", "1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_FALLBACK_DIR", str(DEMO))

    app: Any = testing.AppTest.from_file("../dashboard/app.py").run()

    assert not app.exception
    banner = " ".join(w.value for w in app.warning)
    assert "LIVE SOURCE UNAVAILABLE" in banner
    assert "RECORDED REPLAY — run fixture-synthetic-ltown-demo" in banner
    assert not app.error
