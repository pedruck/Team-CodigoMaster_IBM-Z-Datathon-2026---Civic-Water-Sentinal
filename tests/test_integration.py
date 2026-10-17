"""End-to-end replay of a synthetic day through the HTTP API with the mocks.

Checks plumbing only (ordering, history, alerts, audit). It says nothing about the ability
of the real models to detect leaks.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sentinel.audit.sinks import InMemoryAuditSink
from sentinel.config import Settings
from sentinel.main import build_pipeline, create_app
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import generate


def test_synthetic_day_replay_with_injected_step() -> None:
    settings = Settings(audit_sink="memory")  # documented defaults: 288 / 144 / 300 s
    pipeline = build_pipeline(settings)
    client = TestClient(create_app(settings, pipeline))
    registry = SensorRegistry.load(settings.sensor_registry_path)
    windows = list(
        generate(
            datetime(2019, 3, 14, tzinfo=UTC),
            288,
            registry,
            seed=7,
            noise_m=0.05,
            leak_at=200,
            leak_sensors=registry.pressure[:3],
        )
    )

    statuses = [client.post("/v1/windows", json=w).json()["status"] for w in windows]

    assert statuses[:144] == ["insufficient_history"] * 144
    assert set(statuses[144:]) == {"scored"}
    alerts = client.get("/v1/alerts").json()
    assert alerts["total"] == 1
    alert = alerts["items"][0]
    assert alert["window_end"] == windows[200]["timestamp"]
    assert {d["sensor"] for d in alert["deviating_sensors"]} == set(registry.pressure[:3])
    assert alert["audit"] is not None

    sink = pipeline.audit_sink
    assert isinstance(sink, InMemoryAuditSink)
    assert len(sink.events) == 288 + 1
    status = client.get("/v1/status").json()
    assert status["counters"]["windows_accepted"] == 288
    assert status["history"]["size"] == 288  # capacity 288 past + current: nothing evicted yet
