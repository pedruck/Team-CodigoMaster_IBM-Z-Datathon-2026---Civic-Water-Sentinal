"""Fail-closed audit (Q-AUD-03, ADR-006): an alert is served only after the audit log has it."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.config import Settings
from sentinel.main import create_app
from sentinel.schemas.windows import SensorWindow
from sentinel.services.errors import ServiceUnavailableError
from sentinel.services.pipeline import Pipeline
from tests.doubles import SwitchableAuditSink

Win = Callable[..., SensorWindow]
LEAK: dict[str, Any] = {"drop_m": 5.0, "drop_sensors": ("P1", "P2")}


@pytest.fixture
def sink() -> SwitchableAuditSink:
    return SwitchableAuditSink()


@pytest.fixture
def audited(make_pipeline: Callable[..., Pipeline], sink: SwitchableAuditSink) -> Pipeline:
    return make_pipeline(audit_sink=sink)


def feed(p: Pipeline, window: Win, start: int, stop: int) -> None:
    for i in range(start, stop):
        p.ingest(window(i))


def test_alert_is_withheld_while_audit_is_down(
    audited: Pipeline, sink: SwitchableAuditSink, window: Win
) -> None:
    feed(audited, window, 0, 5)
    sink.healthy = False
    r = audited.ingest(window(5, **LEAK)).result
    assert r.audit == "pending"
    assert len(r.alert_ids) == 1
    assert audited.alerts.get(r.alert_ids[0]) is None  # not served
    assert audited.audit_pending == {"events": 2, "alerts": 1}


def test_pending_events_are_flushed_in_order_when_audit_recovers(
    audited: Pipeline, sink: SwitchableAuditSink, window: Win
) -> None:
    feed(audited, window, 0, 5)
    sink.healthy = False
    alert_id = audited.ingest(window(5, **LEAK)).result.alert_ids[0]
    audited.ingest(window(6))
    sink.healthy = True
    r = audited.ingest(window(7)).result

    assert r.audit == "recorded"
    assert audited.audit_pending == {"events": 0, "alerts": 0}
    alert = audited.alerts.get(alert_id)
    assert alert is not None and alert.audit is not None
    stamps = [e.window_end for e in sink.events]
    assert stamps == sorted(stamps)  # chain order follows window order
    assert [e.type for e in sink.events[5:]] == [
        "alert.raised",
        "window.scored",
        "window.scored",
        "window.scored",
    ]
    assert alert.audit.seq == 5


def test_new_events_queue_behind_pending_ones(
    audited: Pipeline, sink: SwitchableAuditSink, window: Win
) -> None:
    feed(audited, window, 0, 3)
    sink.healthy = False
    audited.ingest(window(3))
    sink.healthy = True
    sink_append = sink.append
    calls: list[str] = []

    def spy(event):  # type: ignore[no-untyped-def]  # test-only wrapper
        calls.append(event.window_end.isoformat())
        return sink_append(event)

    sink.append = spy  # type: ignore[method-assign]  # test-only wrapper
    audited.ingest(window(4))
    assert calls == [window(3).timestamp.isoformat(), window(4).timestamp.isoformat()]


def test_backlog_full_rejects_window_without_changing_state(
    make_pipeline: Callable[..., Pipeline], settings: Settings, window: Win
) -> None:
    sink = SwitchableAuditSink(healthy=False)
    p = make_pipeline(
        audit_sink=sink, settings_override=replace(settings, audit_pending_capacity=2)
    )
    p.ingest(window(0))
    p.ingest(window(1))
    with pytest.raises(ServiceUnavailableError) as exc:
        p.ingest(window(2))
    assert exc.value.code == "audit_backlog_full"
    assert len(p.history) == 2 and p.last_timestamp == window(1).timestamp
    sink.healthy = True
    assert p.ingest(window(2)).result.audit == "recorded"
    assert len(sink.events) == 3


def test_disabled_audit_still_serves_alerts(pipeline: Pipeline, window: Win) -> None:
    feed(pipeline, window, 0, 5)
    r = pipeline.ingest(window(5, **LEAK)).result
    assert r.audit == "disabled"
    assert pipeline.alerts.get(r.alert_ids[0]) is not None


def test_status_and_http_expose_the_backlog(
    settings: Settings,
    make_pipeline: Callable[..., Pipeline],
    payload: Callable[..., dict[str, Any]],
) -> None:
    sink = SwitchableAuditSink(healthy=False)
    p = make_pipeline(
        audit_sink=sink, settings_override=replace(settings, audit_pending_capacity=1)
    )
    client = TestClient(create_app(p.settings, p))
    assert client.post("/v1/windows", json=payload(0)).json()["audit"] == "pending"
    r = client.post("/v1/windows", json=payload(1))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "audit_backlog_full"
    s = client.get("/v1/status").json()
    assert s["audit_pending_events"] == 1
    assert s["audit_pending_alerts"] == 0
    assert s["counters"]["audit_failures"] >= 2


def test_pending_capacity_setting_is_parsed() -> None:
    assert Settings.from_env({}).audit_pending_capacity == 2016
    assert Settings.from_env({"SENTINEL_AUDIT_PENDING_CAPACITY": "10"}).audit_pending_capacity == 10
