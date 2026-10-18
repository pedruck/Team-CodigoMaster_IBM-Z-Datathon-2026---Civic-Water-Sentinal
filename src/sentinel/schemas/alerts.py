"""Public alert payload (``sentinel.alert`` v1.1) and audit contract types."""

from typing import Any, Literal

from pydantic import Field

from sentinel.schemas.common import ContractModel, FiniteFloat, ModelInfo, UtcDatetime
from sentinel.schemas.detections import CusumEvidence, DeviatingSensor
from sentinel.schemas.windows import SensorId

# 1.1 adds ``candidate_pipe`` (optional, Q-DET-07, ADR-011); 1.0 payloads are still accepted.
ALERT_SCHEMA_VERSION: Literal["1.1"] = "1.1"
AlertSchemaVersion = Literal["1.0", "1.1"]

AuditEventType = Literal["window.scored", "window.not_scored", "alert.raised"]


class AuditRef(ContractModel):
    """Reference returned by the audit sink. ``hash`` is null until the hash chain exists."""

    seq: int = Field(ge=0)
    hash: str | None = None


class AlertModels(ContractModel):
    forecaster: ModelInfo
    detector: ModelInfo


class Alert(ContractModel):
    schema_version: AlertSchemaVersion = ALERT_SCHEMA_VERSION
    alert_id: str = Field(description="Deterministic: A-<window_end yyyymmddThhmmZ>-<n>.")
    window_end: UtcDatetime
    created_at: UtcDatetime = Field(description="Server wall-clock time when stored.")
    classification: Literal["leak_suspected"]
    region: str | None
    candidate_pipe: str | None = Field(
        default=None,
        description="Network pipe (link ID) named as the candidate for the leak; null when the "
        "detector has no pipe network. A candidate, not a confirmed location (ADR-011).",
    )
    suspected_onset: UtcDatetime | None
    hours_since_onset: FiniteFloat | None
    cusum: CusumEvidence | None
    deviating_sensors: tuple[DeviatingSensor, ...]
    forecast_contributions_m: dict[SensorId, dict[str, FiniteFloat]] | None = Field(
        description="Forecaster contributions for the deviating sensors, when provided."
    )
    summary: str | None
    models: AlertModels
    is_mock: bool = Field(description="True if the forecaster or detector is a mock.")
    audit: AuditRef | None = Field(description="Null when the audit sink is disabled/failed.")


class AuditEvent(ContractModel):
    """Event handed to the audit sink. The sink owns sequencing, hashing and persistence."""

    type: AuditEventType
    window_end: UtcDatetime
    alert_id: str | None = None
    payload: dict[str, Any]
