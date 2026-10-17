"""Dashboard HTTP adapter and header, against a stub server that replays real API answers.

The stub bodies are produced by the backend itself (``create_app`` + ``TestClient``), so the
tests break if the contract and the dashboard drift apart.
"""

import json
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dashboard.header import build_header
from dashboard.source import (
    AlertNotFoundError,
    AlertQuery,
    ContractMismatchError,
    HttpAlertSource,
    SourceUnavailableError,
)
from sentinel.schemas.api import StatusResponse

Routes = dict[str, tuple[int, bytes]]


class Stub:
    def __init__(self) -> None:
        self.routes: Routes = {}
        self.requests: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                stub.requests.append(self.path)
                code, body = stub.routes.get(self.path.split("?")[0], (404, b"{}"))
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def stub() -> Iterator[Stub]:
    s = Stub()
    thread = threading.Thread(target=s.server.serve_forever, daemon=True)
    thread.start()
    yield s
    s.server.shutdown()
    s.server.server_close()


@pytest.fixture
def api_bodies(client: TestClient, payload: Callable[..., dict[str, Any]]) -> dict[str, bytes]:
    for i in range(4):  # min history is 3 in the test settings: the last window is scored
        assert client.post("/v1/windows", json=payload(i)).status_code == 201
    return {
        "/health": client.get("/health").content,
        "/v1/status": client.get("/v1/status").content,
        "/v1/alerts": client.get("/v1/alerts").content,
    }


def test_parses_real_api_answers(stub: Stub, api_bodies: dict[str, bytes]) -> None:
    stub.routes = {path: (200, body) for path, body in api_bodies.items()}
    source = HttpAlertSource(stub.url)

    assert source.health().status == "ok"
    status = source.status()
    assert status.mocks_active is True
    assert status.last_timestamp == datetime(2019, 1, 1, 0, 15, tzinfo=UTC)
    assert source.list_alerts(AlertQuery()).total == 0


def test_alert_query_is_sent_with_api_parameter_names(
    stub: Stub, api_bodies: dict[str, bytes]
) -> None:
    stub.routes = {"/v1/alerts": (200, api_bodies["/v1/alerts"])}
    since = datetime(2019, 3, 1, tzinfo=UTC)

    HttpAlertSource(stub.url).list_alerts(AlertQuery(limit=10, offset=20, since=since, region="R4"))

    assert stub.requests[-1] == (
        "/v1/alerts?limit=10&offset=20&since=2019-03-01T00%3A00%3A00%2B00%3A00&region=R4"
    )


def test_contract_drift_is_a_contract_mismatch(stub: Stub, api_bodies: dict[str, bytes]) -> None:
    drifted = json.loads(api_bodies["/v1/status"])
    drifted["unexpected_field"] = 1
    stub.routes = {"/v1/status": (200, json.dumps(drifted).encode())}

    with pytest.raises(ContractMismatchError):
        HttpAlertSource(stub.url).status()


def test_missing_alert_is_not_found(stub: Stub) -> None:
    body = b'{"error": {"code": "not_found", "message": "no alert A-1"}}'
    stub.routes = {"/v1/alerts/A-1": (404, body)}

    with pytest.raises(AlertNotFoundError, match="no alert A-1"):
        HttpAlertSource(stub.url).get_alert("A-1")


def test_server_error_is_unavailable(stub: Stub) -> None:
    stub.routes = {"/v1/status": (500, b"{}")}

    with pytest.raises(SourceUnavailableError):
        HttpAlertSource(stub.url).status()


def test_unreachable_backend_is_unavailable() -> None:
    with pytest.raises(SourceUnavailableError):
        HttpAlertSource("http://127.0.0.1:9", timeout_s=1).health()


def test_header_uses_simulated_time_and_flags_mocks(api_bodies: dict[str, bytes]) -> None:
    status = StatusResponse.model_validate_json(api_bodies["/v1/status"])

    header = build_header(status, online=True)

    assert header.simulated_time == "2019-01-01 00:15 UTC"
    assert header.simulated is True
    assert header.forecaster.endswith("(mock)")
    assert header.provisional_sensors is True  # the test registry is provisional


def test_header_without_status_is_unknown_not_simulated() -> None:
    header = build_header(None, online=False)

    assert (header.online, header.simulated_time, header.simulated) == (False, "unknown", False)


def test_app_renders_header_from_backend(
    stub: Stub, api_bodies: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    stub.routes = {path: (200, body) for path, body in api_bodies.items()}
    monkeypatch.setenv("SENTINEL_DASHBOARD_API_URL", stub.url)

    app = testing.AppTest.from_file("../dashboard/app.py").run()

    assert not app.exception
    assert any("SIMULATED" in w.value for w in app.warning)
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Simulated time"] == "2019-01-01 00:15 UTC"
    assert metrics["API"] == "online"


def test_app_shows_unavailable_when_backend_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SENTINEL_DASHBOARD_TIMEOUT_S", "1")

    app = testing.AppTest.from_file("../dashboard/app.py").run()

    assert not app.exception
    assert any("API unavailable" in e.value for e in app.error)
    assert {m.label: m.value for m in app.metric}["API"] == "offline"
