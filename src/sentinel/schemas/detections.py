"""Detector contract (``sentinel.detection`` v1.0). See docs/integration-contracts.md §3."""

from typing import Literal

from pydantic import Field

from sentinel.schemas.common import ContractModel, FiniteFloat, ModelInfo, UtcDatetime
from sentinel.schemas.windows import SensorId

DetectorState = Literal["normal", "suspect", "alarm"]


class SensorResidual(ContractModel):
    residual_m: FiniteFloat = Field(description="observed - expected, in metres.")
    z: FiniteFloat | None = Field(default=None, description="Standardised residual, if computed.")
    cusum: FiniteFloat | None = Field(default=None, description="Sensor CUSUM statistic, if any.")


class CusumEvidence(ContractModel):
    statistic: FiniteFloat
    threshold: FiniteFloat


class DeviatingSensor(ContractModel):
    sensor: SensorId
    deviation_m: FiniteFloat
    z: FiniteFloat | None = None
    since: UtcDatetime | None = None


class AlertCandidate(ContractModel):
    """An alert opened by the detector at this window. The backend assigns the ID."""

    classification: Literal["leak_suspected"] = "leak_suspected"
    region: str | None = Field(default=None, description="Estimated region, when available.")
    suspected_onset: UtcDatetime | None = None
    cusum: CusumEvidence | None = None
    deviating_sensors: tuple[DeviatingSensor, ...] = Field(min_length=1)
    summary: str | None = Field(default=None, max_length=500)


class DetectionResult(ContractModel):
    timestamp: UtcDatetime = Field(description="Must equal the evaluated window timestamp.")
    model: ModelInfo
    state: DetectorState
    anomaly_score: FiniteFloat | None = None
    residuals: dict[SensorId, SensorResidual]
    alerts: tuple[AlertCandidate, ...] = ()
    region_scores: dict[str, FiniteFloat] | None = None
    diagnostics: dict[str, FiniteFloat | int | str | bool] = Field(default_factory=dict)
    complete: bool = Field(
        default=True, description="False when the detector processed only part of the sensors."
    )
    issues: tuple[str, ...] = ()
