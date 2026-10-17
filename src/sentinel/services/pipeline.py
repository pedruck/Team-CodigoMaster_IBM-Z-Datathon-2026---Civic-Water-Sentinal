"""Orchestration of one window: validation -> temporal policy -> history -> forecast ->
detection -> alerts -> audit. Policies: ADR-004 (temporal) and ADR-006 (errors/idempotency).
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sentinel.audit.sinks import AuditSink
from sentinel.config import Settings
from sentinel.detection.interface import Detector
from sentinel.forecasting.interface import Forecaster
from sentinel.observability.metrics import Metrics, StageTimer
from sentinel.schemas.alerts import Alert, AlertModels, AuditEvent, AuditRef
from sentinel.schemas.api import (
    AuditStatus,
    HistoryInfo,
    ProcessingIssue,
    ProcessingStatus,
    WindowResult,
)
from sentinel.schemas.detections import AlertCandidate, DetectionResult
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult
from sentinel.schemas.windows import SensorWindow
from sentinel.sensors import SensorRegistry
from sentinel.services.errors import SentinelError, TemporalConflictError
from sentinel.services.history import HistoryBuffer
from sentinel.services.ingestion import validate_window
from sentinel.services.stores import AlertStore, ResultStore

log = logging.getLogger("sentinel.pipeline")


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    result: WindowResult
    created: bool


def alert_id_for(window_end: datetime, index: int) -> str:
    """Deterministic ID, so replaying the same data yields the same IDs across restarts."""
    return f"A-{window_end:%Y%m%dT%H%M}Z-{index}"


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        registry: SensorRegistry,
        forecaster: Forecaster,
        detector: Detector,
        audit_sink: AuditSink,
        metrics: Metrics,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.forecaster = forecaster
        self.detector = detector
        self.audit_sink = audit_sink
        self.metrics = metrics
        self._clock = clock
        # +1: the buffer also holds the window being scored, which is never passed as history.
        self.history = HistoryBuffer(settings.history_capacity + 1, settings.interval_seconds)
        self.results = ResultStore(settings.result_capacity)
        self.alerts = AlertStore(settings.alert_capacity)
        self._lock = threading.Lock()
        self._last_ts: datetime | None = None

    @property
    def required_history(self) -> int:
        return max(self.settings.min_history_windows, self.forecaster.min_history_windows)

    @property
    def mocks_active(self) -> bool:
        return self.forecaster.info.is_mock or self.detector.info.is_mock

    @property
    def last_timestamp(self) -> datetime | None:
        with self._lock:
            return self._last_ts

    def history_info(self) -> HistoryInfo:
        return HistoryInfo(
            size=len(self.history),
            contiguous=self.history.contiguous,
            required=self.required_history,
            capacity=self.settings.history_capacity,
        )

    # ------------------------------------------------------------------ ingest

    def ingest(self, window: SensorWindow) -> IngestOutcome:
        try:
            return self._ingest(window)
        except SentinelError as exc:
            self.metrics.inc(f"windows_rejected_{exc.code}")
            raise

    def _ingest(self, window: SensorWindow) -> IngestOutcome:
        timer = StageTimer()
        t = time.perf_counter()
        validate_window(
            window,
            self.registry,
            self.settings.interval_seconds,
            self.settings.require_aligned_timestamps,
        )
        timer.measure("validation", t)

        # One window at a time: the detector is stateful and must see windows in order.
        with self._lock:
            t = time.perf_counter()
            content_hash = window.content_hash()
            stored = self.results.get(window.timestamp)
            if stored is not None:
                if stored[0] != content_hash:
                    raise TemporalConflictError(
                        "window_conflict",
                        "a different window with this timestamp was already accepted",
                        {"timestamp": window.timestamp.isoformat()},
                    )
                self.metrics.inc("windows_duplicate")
                return IngestOutcome(stored[1].model_copy(update={"duplicate": True}), False)
            if self._last_ts is not None and window.timestamp <= self._last_ts:
                raise TemporalConflictError(
                    "out_of_order",
                    "window is not newer than the last accepted window",
                    {
                        "timestamp": window.timestamp.isoformat(),
                        "last_timestamp": self._last_ts.isoformat(),
                    },
                )
            timer.measure("temporal_policy", t)

            t = time.perf_counter()
            gap = self.history.append(window)
            self._last_ts = window.timestamp
            timer.measure("history", t)

            result = self._process(window, content_hash, gap, timer)
            self.results.put(window.timestamp, content_hash, result)

        self._record(result, timer)
        return IngestOutcome(result, True)

    def _process(
        self, window: SensorWindow, content_hash: str, gap: int, timer: StageTimer
    ) -> WindowResult:
        issues: list[ProcessingIssue] = []
        status: ProcessingStatus
        detection: DetectionResult | None = None
        forecast: ForecastResult | None = None
        alerts: list[Alert] = []

        past = self.history.contiguous_tail(self.settings.history_capacity, exclude_last=True)
        if len(past) < self.required_history:
            status = "insufficient_history"
        else:
            forecast = self._forecast(window, past, timer, issues)
            if forecast is None:
                status = "forecast_failed"
            else:
                detection = self._detect(window, forecast, timer, issues)
                status = "scored" if detection is not None else "detection_failed"
                if detection is not None:
                    alerts = [
                        self._build_alert(window.timestamp, i, c, forecast, detection)
                        for i, c in enumerate(detection.alerts, start=1)
                    ]

        t = time.perf_counter()
        audit_status, alerts = self._audit(
            window, status, alerts, detection, gap, content_hash, issues
        )
        timer.measure("audit", t)
        for alert in alerts:
            self.alerts.add(alert)

        timer.finish()
        return WindowResult(
            timestamp=window.timestamp,
            status=status,
            gap_windows=gap,
            history=self.history_info(),
            detector_state=detection.state if detection else None,
            alert_ids=tuple(a.alert_id for a in alerts),
            forecaster=self.forecaster.info if status != "insufficient_history" else None,
            detector=self.detector.info if detection else None,
            issues=tuple(issues),
            audit=audit_status,
            latency_ms=timer.as_ms(),
        )

    def _forecast(
        self,
        window: SensorWindow,
        past: tuple[SensorWindow, ...],
        timer: StageTimer,
        issues: list[ProcessingIssue],
    ) -> ForecastResult | None:
        t = time.perf_counter()
        request = ForecastRequest(
            target_timestamp=window.timestamp,
            pressure_sensor_ids=self.registry.pressure,
            history=past,
            current=window,
        )
        try:
            forecast = self.forecaster.predict(request)
        except Exception as exc:  # boundary with a pluggable module owned by another member
            log.exception("forecast failed", extra={"fields": {"window": window.timestamp}})
            issues.append(
                ProcessingIssue(stage="forecast", code=type(exc).__name__, message=str(exc))
            )
            return None
        finally:
            timer.measure("forecast", t)
        problem = _check_forecast(forecast, window.timestamp, self.registry.pressure)
        if problem:
            issues.append(
                ProcessingIssue(stage="forecast", code="invalid_forecast", message=problem)
            )
            return None
        return forecast

    def _detect(
        self,
        window: SensorWindow,
        forecast: ForecastResult,
        timer: StageTimer,
        issues: list[ProcessingIssue],
    ) -> DetectionResult | None:
        t = time.perf_counter()
        observed = {s: window.pressures[s] for s in self.registry.pressure}
        try:
            detection = self.detector.evaluate(
                observed, forecast.expected_pressures, window.timestamp
            )
        except Exception as exc:  # boundary with a pluggable module owned by another member
            log.exception("detection failed", extra={"fields": {"window": window.timestamp}})
            issues.append(
                ProcessingIssue(stage="detection", code=type(exc).__name__, message=str(exc))
            )
            return None
        finally:
            timer.measure("detection", t)
        if detection.timestamp != window.timestamp:
            issues.append(
                ProcessingIssue(
                    stage="detection",
                    code="invalid_detection",
                    message="DetectionResult.timestamp differs from the evaluated window",
                )
            )
            return None
        if not detection.complete:
            issues.append(
                ProcessingIssue(
                    stage="detection",
                    code="incomplete_detection",
                    message="; ".join(detection.issues) or "detector reported partial processing",
                )
            )
        return detection

    def _build_alert(
        self,
        window_end: datetime,
        index: int,
        candidate: AlertCandidate,
        forecast: ForecastResult,
        detection: DetectionResult,
    ) -> Alert:
        onset = candidate.suspected_onset
        deviating = {d.sensor for d in candidate.deviating_sensors}
        contributions = (
            {s: c for s, c in forecast.contributions.items() if s in deviating}
            if forecast.contributions
            else None
        )
        return Alert(
            alert_id=alert_id_for(window_end, index),
            window_end=window_end,
            created_at=self._clock(),
            classification=candidate.classification,
            region=candidate.region,
            suspected_onset=onset,
            hours_since_onset=(
                round((window_end - onset).total_seconds() / 3600, 2) if onset else None
            ),
            cusum=candidate.cusum,
            deviating_sensors=candidate.deviating_sensors,
            forecast_contributions_m=contributions or None,
            summary=candidate.summary,
            models=AlertModels(forecaster=forecast.model, detector=detection.model),
            is_mock=forecast.model.is_mock or detection.model.is_mock,
            audit=None,
        )

    def _audit(
        self,
        window: SensorWindow,
        status: ProcessingStatus,
        alerts: list[Alert],
        detection: DetectionResult | None,
        gap: int,
        content_hash: str,
        issues: list[ProcessingIssue],
    ) -> tuple[AuditStatus, list[Alert]]:
        """Alerts are kept even if auditing fails (fail-open, ADR-006 — Proposed)."""
        if not self.audit_sink.enabled:
            return "disabled", alerts
        failed = False
        audited: list[Alert] = []
        for alert in alerts:
            ref = self._append(
                AuditEvent(
                    type="alert.raised",
                    window_end=window.timestamp,
                    alert_id=alert.alert_id,
                    payload=alert.model_dump(mode="json", exclude={"audit"}),
                ),
                issues,
            )
            failed |= ref is None
            audited.append(alert.model_copy(update={"audit": ref}))
        payload: dict[str, Any] = {
            "status": status,
            "content_hash": content_hash,
            "gap_windows": gap,
            "detector_state": detection.state if detection else None,
            "anomaly_score": detection.anomaly_score if detection else None,
            "alert_ids": [a.alert_id for a in audited],
            "forecaster": self.forecaster.info.model_dump(),
            "detector": self.detector.info.model_dump(),
            "issues": [i.model_dump() for i in issues],
        }
        ref = self._append(
            AuditEvent(
                type="window.scored" if status == "scored" else "window.not_scored",
                window_end=window.timestamp,
                payload=payload,
            ),
            issues,
        )
        failed |= ref is None
        return ("failed" if failed else "recorded"), audited

    def _append(self, event: AuditEvent, issues: list[ProcessingIssue]) -> AuditRef | None:
        try:
            return self.audit_sink.append(event)
        except Exception as exc:  # boundary with a pluggable module owned by another member
            log.exception("audit append failed", extra={"fields": {"type": event.type}})
            self.metrics.inc("audit_failures")
            issues.append(ProcessingIssue(stage="audit", code=type(exc).__name__, message=str(exc)))
            return None

    def _record(self, result: WindowResult, timer: StageTimer) -> None:
        for stage, seconds in timer.durations.items():
            self.metrics.observe(stage, seconds)
        self.metrics.inc("windows_accepted")
        self.metrics.inc(f"windows_status_{result.status}")
        if result.gap_windows:
            self.metrics.inc("windows_after_gap")
            self.metrics.inc("missing_windows", result.gap_windows)
        if result.alert_ids:
            self.metrics.inc("alerts_raised", len(result.alert_ids))
        log.info(
            "window processed",
            extra={
                "fields": {
                    "window": result.timestamp.isoformat(),
                    "status": result.status,
                    "gap_windows": result.gap_windows,
                    "alerts": list(result.alert_ids),
                    "audit": result.audit,
                    "total_ms": result.latency_ms.get("total"),
                }
            },
        )


def _check_forecast(
    forecast: ForecastResult, target: datetime, sensors: tuple[str, ...]
) -> str | None:
    if forecast.timestamp != target:
        return f"forecast timestamp {forecast.timestamp.isoformat()} != target {target.isoformat()}"
    missing = sorted(set(sensors) - forecast.expected_pressures.keys())
    extra = sorted(forecast.expected_pressures.keys() - set(sensors))
    if missing or extra:
        return f"expected_pressures sensor mismatch: missing={missing} unexpected={extra}"
    return None
