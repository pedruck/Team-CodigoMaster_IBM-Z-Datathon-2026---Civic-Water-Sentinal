import json
import threading
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from sentinel.config import Settings
from sentinel.main import create_app
from sentinel.services.pipeline import Pipeline
from tests.conftest import ts
from tests.doubles import FlakyForecaster

Payload = Callable[..., dict[str, Any]]
LEAK: dict[str, Any] = {"drop_m": 5.0, "drop_sensors": ("P1", "P2")}


def post_range(client: TestClient, payload: Payload, n: int, start: int = 0) -> None:
    for i in range(start, n):
        assert client.post("/v1/windows", json=payload(i)).status_code == 201


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_valid_window_returns_201_with_result(client: TestClient, payload: Payload) -> None:
    r = client.post("/v1/windows", json=payload(0))
    assert r.status_code == 201
    body = r.json()
    assert body["timestamp"] == "2019-01-01T00:00:00Z"
    assert body["status"] == "insufficient_history"
    assert body["history"] == {"size": 1, "contiguous": 1, "required": 3, "capacity": 6}
    assert set(body["latency_ms"]) >= {"validation", "history", "total"}


def test_duplicate_returns_200_and_same_result(client: TestClient, payload: Payload) -> None:
    first = client.post("/v1/windows", json=payload(0)).json()
    r = client.post("/v1/windows", json={**payload(0), "source": "another-run"})
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert r.json()["latency_ms"] == first["latency_ms"]


def test_unparseable_timestamp_returns_422_envelope(client: TestClient, payload: Payload) -> None:
    r = client.post("/v1/windows", json={**payload(0), "timestamp": "14/03/2019 12:30"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"][0]["loc"][-1] == "timestamp"


def test_nan_in_raw_json_is_rejected(client: TestClient, payload: Payload) -> None:
    p = payload(0)
    p["pressures"]["P1"] = float("nan")
    raw = json.dumps(p)  # Python emits the non-standard NaN token
    assert '"P1": NaN' in raw
    r = client.post("/v1/windows", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_unknown_field_is_rejected(client: TestClient, payload: Payload) -> None:
    r = client.post("/v1/windows", json={**payload(0), "label_leak": True})
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["type"] == "extra_forbidden"


def test_missing_sensor_returns_sensor_set_mismatch(client: TestClient, payload: Payload) -> None:
    p = payload(0)
    del p["tank_levels"]["T1"]
    r = client.post("/v1/windows", json=p)
    assert r.status_code == 422
    assert r.json()["error"] == {
        "code": "sensor_set_mismatch",
        "message": "window sensors do not match the sensor registry",
        "details": {"missing_tank_levels": ["T1"]},
    }


def test_out_of_order_and_conflict_return_409(client: TestClient, payload: Payload) -> None:
    client.post("/v1/windows", json=payload(1))
    old = client.post("/v1/windows", json=payload(0))
    conflict = client.post("/v1/windows", json=payload(1, drop_m=1.0, drop_sensors=("P3",)))
    assert (old.status_code, old.json()["error"]["code"]) == (409, "out_of_order")
    assert (conflict.status_code, conflict.json()["error"]["code"]) == (409, "window_conflict")


def test_alert_is_exposed_by_list_and_detail(client: TestClient, payload: Payload) -> None:
    post_range(client, payload, 5)
    r = client.post("/v1/windows", json=payload(5, **LEAK)).json()
    assert r["status"] == "scored" and r["detector_state"] == "alarm"
    alert_id = r["alert_ids"][0]

    page = client.get("/v1/alerts").json()
    assert page["total"] == 1 and page["items"][0]["alert_id"] == alert_id

    detail = client.get(f"/v1/alerts/{alert_id}")
    assert detail.status_code == 200
    a = detail.json()
    assert a["schema_version"] == "1.1"
    assert a["candidate_pipe"] is None  # the mock detector has no pipe network
    assert a["window_end"] == "2019-01-01T00:25:00Z"
    assert a["is_mock"] is True
    assert a["models"]["detector"]["name"] == "mock-threshold"
    assert a["audit"] is None


def test_unknown_alert_returns_404_envelope(client: TestClient) -> None:
    r = client.get("/v1/alerts/A-nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_alert_filters_and_pagination(client: TestClient, payload: Payload) -> None:
    post_range(client, payload, 4)
    for i in (4, 6, 8):  # each step re-arms the mock: alarm, normal, alarm...
        client.post("/v1/windows", json=payload(i, **LEAK))
        client.post("/v1/windows", json=payload(i + 1))
    all_ = client.get("/v1/alerts").json()
    assert all_["total"] == 3
    stamps = [a["window_end"] for a in all_["items"]]
    assert stamps == sorted(stamps, reverse=True)
    page = client.get("/v1/alerts", params={"limit": 1, "offset": 1}).json()
    assert len(page["items"]) == 1 and page["items"][0]["window_end"] == stamps[1]
    since = client.get("/v1/alerts", params={"since": ts(6).isoformat()}).json()
    assert since["total"] == 2
    assert client.get("/v1/alerts", params={"region": "R4"}).json()["total"] == 0
    assert client.get("/v1/alerts", params={"limit": 0}).status_code == 422
    assert client.get("/v1/alerts", params={"since": "2019-01-01T00:00:00"}).status_code == 422


def test_status_reports_counters_latency_and_mocks(client: TestClient, payload: Payload) -> None:
    post_range(client, payload, 5)
    client.post("/v1/windows", json=payload(0))  # duplicate
    client.post("/v1/windows", json={**payload(9), "timestamp": "bad"})  # 422
    s = client.get("/v1/status").json()
    assert s["mocks_active"] is True
    assert s["sensor_registry"]["provisional"] is True
    assert s["persistence"] == "memory"
    assert s["last_timestamp"] == "2019-01-01T00:20:00Z"
    assert s["counters"]["windows_accepted"] == 5
    assert s["counters"]["windows_duplicate"] == 1
    assert s["counters"]["windows_status_scored"] == 2
    assert s["counters"]["http_windows_requests"] == 7
    assert s["error_rate"] == round(1 / 7, 6)
    assert s["latency"]["forecast"]["count"] == 2
    assert s["latency"]["total"]["p99_ms"] is not None


def test_forecast_failure_is_201_with_status(
    settings: Settings, make_pipeline: Callable[..., Pipeline], payload: Payload
) -> None:
    client = TestClient(
        create_app(settings, make_pipeline(forecaster=FlakyForecaster(fail_at={ts(3)})))
    )
    post_range(client, payload, 3)
    r = client.post("/v1/windows", json=payload(3))
    assert r.status_code == 201
    assert r.json()["status"] == "forecast_failed"
    assert r.json()["issues"][0]["code"] == "ForecastError"


def test_concurrent_requests_are_serialised(client: TestClient, payload: Payload) -> None:
    codes: list[int] = []
    lock = threading.Lock()

    def send(i: int) -> None:
        code = client.post("/v1/windows", json=payload(i)).status_code
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=send, args=(i % 10,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(codes) <= {200, 201, 409}
    s = client.get("/v1/status").json()
    assert codes.count(201) == s["counters"]["windows_accepted"]
    assert s["history"]["size"] == min(7, codes.count(201))


def test_openapi_documents_the_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/health", "/v1/windows", "/v1/status", "/v1/alerts", "/v1/alerts/{alert_id}"} <= set(
        paths
    )
