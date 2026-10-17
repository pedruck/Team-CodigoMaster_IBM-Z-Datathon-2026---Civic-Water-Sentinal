"""HTTP response bodies and the uniform error envelope."""

from typing import Any, Literal

from pydantic import Field

from sentinel.schemas.alerts import Alert
from sentinel.schemas.common import ContractModel, ModelInfo, UtcDatetime
from sentinel.schemas.detections import DetectorState

ProcessingStatus = Literal["scored", "insufficient_history", "forecast_failed", "detection_failed"]
AuditStatus = Literal["recorded", "pending", "disabled"]
IssueStage = Literal["forecast", "detection", "audit"]


class ProcessingIssue(ContractModel):
    stage: IssueStage
    code: str
    message: str


class HistoryInfo(ContractModel):
    size: int
    contiguous: int
    required: int
    capacity: int


class WindowResult(ContractModel):
    """Outcome of ingesting one window. Stored and returned again for idempotent resends."""

    timestamp: UtcDatetime
    timestamp_tz_assumed: bool = Field(
        default=False,
        description="True if the window came without timezone and UTC was assumed (ADR-004).",
    )
    status: ProcessingStatus
    duplicate: bool = False
    gap_windows: int = Field(ge=0, description="Missing intervals right before this window.")
    history: HistoryInfo
    detector_state: DetectorState | None
    alert_ids: tuple[str, ...]
    forecaster: ModelInfo | None
    detector: ModelInfo | None
    issues: tuple[ProcessingIssue, ...]
    audit: AuditStatus
    latency_ms: dict[str, float]


class AlertPage(ContractModel):
    items: tuple[Alert, ...]
    total: int
    limit: int
    offset: int


class LatencySummary(ContractModel):
    count: int
    mean_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    max_ms: float | None


class StatusResponse(ContractModel):
    service: str
    version: str
    started_at: UtcDatetime
    uptime_s: float
    interval_seconds: int
    last_timestamp: UtcDatetime | None
    history: HistoryInfo
    sensor_registry: dict[str, Any]
    forecaster: ModelInfo
    detector: ModelInfo
    audit_sink: str
    audit_pending_events: int = Field(description="Audit events waiting for the sink.")
    audit_pending_alerts: int = Field(description="Alerts withheld until audited (fail-closed).")
    mocks_active: bool
    counters: dict[str, int]
    latency: dict[str, LatencySummary]
    throughput_windows_per_s: float
    error_rate: float
    persistence: Literal["memory"] = "memory"


class HealthResponse(ContractModel):
    status: Literal["ok"]
    version: str


class ErrorBody(ContractModel):
    code: str
    message: str
    details: list[Any] | dict[str, Any] | None = None


class ErrorResponse(ContractModel):
    error: ErrorBody
