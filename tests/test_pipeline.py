"""Orchestration behaviour with mocks (no HTTP)."""

import threading
from collections.abc import Callable

import pytest

from sentinel.audit.sinks import InMemoryAuditSink
from sentinel.schemas.windows import SensorWindow
from sentinel.services.errors import TemporalConflictError
from sentinel.services.pipeline import Pipeline
from tests.conftest import ts
from tests.doubles import (
    CountingDetector,
    FailingAuditSink,
    FlakyForecaster,
    PartialForecaster,
)

Win = Callable[..., SensorWindow]
LEAK = {"leak_sensors": ("P1", "P2")}


def feed(p: Pipeline, window: Win, upto: int, start: int = 0) -> None:
    for i in range(start, upto):
        p.ingest(window(i))


def test_insufficient_history_until_required_windows(pipeline: Pipeline, window: Win) -> None:
    statuses = [pipeline.ingest(window(i)).result.status for i in range(5)]
    # required = 3 past windows: windows 0..2 lack history, window 3 is the first scored.
    assert statuses == ["insufficient_history"] * 3 + ["scored"] * 2


def test_forecaster_receives_only_past_contiguous_windows(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    f = FlakyForecaster()
    p = make_pipeline(forecaster=f)
    feed(p, window, 9)
    req = f.requests[-1]
    stamps = [w.timestamp for w in req.history]
    assert req.target_timestamp == ts(8)
    assert stamps == [ts(i) for i in range(2, 8)]  # capacity 6, strictly before target
    assert req.current.timestamp == ts(8)
    assert req.pressure_sensor_ids == ("P1", "P2", "P3")


def test_duplicate_is_idempotent_and_not_reprocessed(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    d = CountingDetector()
    p = make_pipeline(detector=d)
    feed(p, window, 5)
    calls = len(d.calls)
    again = p.ingest(window(4))
    assert again.created is False
    assert again.result.duplicate is True
    assert again.result.status == "scored"
    assert len(d.calls) == calls
    assert len(p.history) == 5


def test_same_timestamp_with_different_values_is_a_conflict(
    pipeline: Pipeline, window: Win
) -> None:
    pipeline.ingest(window(0))
    with pytest.raises(TemporalConflictError) as exc:
        pipeline.ingest(window(0, drop_m=1.0, drop_sensors=("P1",)))
    assert exc.value.code == "window_conflict"


def test_out_of_order_window_is_rejected_and_not_stored(pipeline: Pipeline, window: Win) -> None:
    pipeline.ingest(window(0))
    pipeline.ingest(window(2))
    with pytest.raises(TemporalConflictError) as exc:
        pipeline.ingest(window(1))
    assert exc.value.code == "out_of_order"
    assert len(pipeline.history) == 2


def test_gap_resets_contiguity_and_requires_history_again(pipeline: Pipeline, window: Win) -> None:
    feed(pipeline, window, 5)
    r = pipeline.ingest(window(8)).result  # 5, 6 and 7 are missing
    assert r.gap_windows == 3
    assert r.status == "insufficient_history"
    assert r.history.contiguous == 1


def test_history_is_bounded_by_capacity(pipeline: Pipeline, window: Win) -> None:
    feed(pipeline, window, 30)
    info = pipeline.history_info()
    assert info.size == 7  # 6 past windows + the current one
    assert info.capacity == 6


def test_forecast_failure_keeps_window_and_pipeline_continues(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    f = FlakyForecaster(fail_at={ts(4)})
    p = make_pipeline(forecaster=f)
    feed(p, window, 4)
    failed = p.ingest(window(4)).result
    assert failed.status == "forecast_failed"
    assert failed.issues[0].stage == "forecast" and failed.issues[0].code == "ForecastError"
    assert p.last_timestamp == ts(4)
    assert p.ingest(window(5)).result.status == "scored"


def test_resending_failed_window_returns_stored_result_without_retry(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    f = FlakyForecaster(fail_at={ts(4)})
    p = make_pipeline(forecaster=f)
    feed(p, window, 5)
    n = len(f.requests)
    again = p.ingest(window(4)).result
    assert again.status == "forecast_failed" and again.duplicate
    assert len(f.requests) == n


def test_unexpected_forecaster_exception_is_contained(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    p = make_pipeline(forecaster=FlakyForecaster(fail_at={ts(3)}, exc=RuntimeError("boom")))
    feed(p, window, 3)
    r = p.ingest(window(3)).result
    assert (r.status, r.issues[0].code) == ("forecast_failed", "RuntimeError")


def test_forecast_violating_contract_is_rejected(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    p = make_pipeline(forecaster=PartialForecaster())
    feed(p, window, 3)
    r = p.ingest(window(3)).result
    assert r.status == "forecast_failed"
    assert r.issues[0].code == "invalid_forecast" and "P3" in r.issues[0].message


def test_detector_failure_is_reported_and_pipeline_continues(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    p = make_pipeline(detector=CountingDetector(fail_at={ts(3)}))
    feed(p, window, 3)
    r = p.ingest(window(3)).result
    assert r.status == "detection_failed"
    assert r.issues[0].stage == "detection" and r.alert_ids == ()
    assert p.ingest(window(4)).result.status == "scored"


def test_alert_is_raised_once_by_the_single_stateful_detector(
    pipeline: Pipeline, window: Win
) -> None:
    feed(pipeline, window, 5)
    # Persistence forecast + step drop: only the step window deviates; afterwards the mock
    # "predicts" the dropped value. Mock behaviour, not a property of the real detector.
    r5 = pipeline.ingest(window(5, drop_m=5.0, drop_sensors=("P1", "P2"))).result
    r6 = pipeline.ingest(window(6, drop_m=5.0, drop_sensors=("P1", "P2"))).result
    assert r5.detector_state == "alarm" and len(r5.alert_ids) == 1
    assert r6.detector_state == "normal" and r6.alert_ids == ()
    alert = pipeline.alerts.get(r5.alert_ids[0])
    assert alert is not None
    assert alert.alert_id == "A-20190101T0025Z-1"
    assert alert.is_mock is True
    assert {d.sensor for d in alert.deviating_sensors} == {"P1", "P2"}
    assert alert.hours_since_onset == 0.0


def test_sustained_alarm_does_not_duplicate_alerts(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    class Constant(FlakyForecaster):
        def predict(self, request):  # type: ignore[no-untyped-def]  # test-only override
            r = super().predict(request)
            return r.model_copy(
                update={"expected_pressures": dict.fromkeys(r.expected_pressures, 60.0)}
            )

    p = make_pipeline(forecaster=Constant())
    results = [p.ingest(window(i)).result for i in range(8)]
    raised = [r for r in results if r.alert_ids]
    assert len(raised) == 1
    assert all(r.detector_state == "alarm" for r in results[3:])


def test_audit_records_alert_and_every_window(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    sink = InMemoryAuditSink()
    p = make_pipeline(audit_sink=sink)
    feed(p, window, 5)
    r = p.ingest(window(5, drop_m=5.0, drop_sensors=("P1", "P2"))).result
    types = [e.type for e in sink.events]
    assert types.count("window.not_scored") == 3
    assert types.count("window.scored") == 3
    assert types.count("alert.raised") == 1
    assert r.audit == "recorded"
    alert = p.alerts.get(r.alert_ids[0])
    assert alert is not None and alert.audit is not None
    raised = next(e for e in sink.events if e.type == "alert.raised")
    assert alert.audit.seq == sink.events.index(raised)
    assert "audit" not in raised.payload


def test_audit_failure_withholds_alert_and_keeps_events_pending(
    make_pipeline: Callable[..., Pipeline], window: Win
) -> None:
    p = make_pipeline(audit_sink=FailingAuditSink())
    first = p.ingest(window(0)).result
    feed(p, window, 5, start=1)
    r = p.ingest(window(5, drop_m=5.0, drop_sensors=("P1", "P2"))).result
    assert first.audit == "pending" and any(i.stage == "audit" for i in first.issues)
    assert r.audit == "pending"
    assert len(r.alert_ids) == 1 and p.alerts.get(r.alert_ids[0]) is None  # fail-closed
    # 6 window events + 1 alert event waiting; 1 failed append + 5 failed retries.
    assert p.audit_pending == {"events": 7, "alerts": 1}
    assert p.metrics.counters()["audit_failures"] == 6


def test_disabled_audit_is_reported(pipeline: Pipeline, window: Win) -> None:
    assert pipeline.ingest(window(0)).result.audit == "disabled"


def test_restart_loses_in_memory_state(make_pipeline: Callable[..., Pipeline], window: Win) -> None:
    feed(make_pipeline(), window, 5)
    fresh = make_pipeline()  # what a process restart looks like
    assert len(fresh.history) == 0 and fresh.last_timestamp is None
    assert fresh.ingest(window(5)).result.status == "insufficient_history"


def test_concurrent_resend_of_same_window_creates_it_once(pipeline: Pipeline, window: Win) -> None:
    w = window(0)
    created: list[bool] = []
    barrier = threading.Barrier(8)

    def send() -> None:
        barrier.wait()
        created.append(pipeline.ingest(w).created)

    threads = [threading.Thread(target=send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(created) == [False] * 7 + [True]
    assert len(pipeline.history) == 1


def test_concurrent_distinct_windows_never_corrupt_order(pipeline: Pipeline, window: Win) -> None:
    windows = [window(i) for i in range(40)]
    accepted: list[int] = []
    lock = threading.Lock()

    def send(i: int) -> None:
        try:
            pipeline.ingest(windows[i])
            with lock:
                accepted.append(i)
        except TemporalConflictError:
            pass  # expected for windows that lost the race; counted via metrics below

    threads = [threading.Thread(target=send, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stamps = [w.timestamp for w in pipeline.history.contiguous_tail()]
    assert stamps == sorted(stamps)
    rejected = pipeline.metrics.counters().get("windows_rejected_out_of_order", 0)
    assert len(accepted) + rejected == 40


def test_pressure_drop_has_negative_deviation_and_positive_z(
    pipeline: Pipeline, window: Win
) -> None:
    # Q-DET-02: deviation_m = observed - expected (< 0), z = (expected - observed)/sigma (> 0).
    feed(pipeline, window, 5)
    r = pipeline.ingest(window(5, drop_m=5.0, drop_sensors=("P1", "P2"))).result
    alert = pipeline.alerts.get(r.alert_ids[0])
    assert alert is not None
    for d in alert.deviating_sensors:
        assert d.deviation_m < 0
        assert d.z is not None and d.z > 0
