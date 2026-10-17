"""Alert detail: the structured evidence exactly as received (no recomputation, no rounding).

Numbers are shown with Python's own ``str`` of the received float. ``created_at`` (server
wall clock) appears only here, labelled "recorded by server". The audit reference is shown
raw; nothing here ever claims it was checked. The narrative slot is always "explanation
unavailable" until an explanation endpoint exists (Q-AUD-07).
"""

from dataclasses import dataclass
from datetime import datetime

from dashboard.alert_list import NO_REGION, NOT_PROVIDED, fmt_time
from sentinel.schemas.alerts import Alert, AuditRef
from sentinel.schemas.common import ModelInfo

AUDIT_UNAVAILABLE = "audit unavailable"
EXPLANATION_UNAVAILABLE = "explanation unavailable"

Row = dict[str, str]


@dataclass(frozen=True)
class AlertDetail:
    alert_id: str
    simulated: bool
    facts: tuple[Row, ...]
    sensors: tuple[Row, ...]
    contributions: tuple[Row, ...] | None
    explanation: str = EXPLANATION_UNAVAILABLE


def _num(value: float | None) -> str:
    return NOT_PROVIDED if value is None else str(value)


def _model(info: ModelInfo) -> str:
    return f"{info.name} {info.version}" + (" (mock)" if info.is_mock else "")


def _audit(ref: AuditRef | None) -> str:
    if ref is None:
        return AUDIT_UNAVAILABLE
    return f"seq {ref.seq}, hash {ref.hash if ref.hash is not None else NOT_PROVIDED}"


def _recorded(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def alert_detail(alert: Alert) -> AlertDetail:
    cusum = (
        f"{alert.cusum.statistic} vs threshold {alert.cusum.threshold}"
        if alert.cusum is not None
        else NOT_PROVIDED
    )
    facts = {
        "Alert": alert.alert_id,
        "Window end": fmt_time(alert.window_end),
        "Region": alert.region or NO_REGION,
        "CUSUM statistic vs threshold": cusum,
        "Estimated onset": fmt_time(alert.suspected_onset),
        "Hours since onset (backend)": _num(alert.hours_since_onset),
        "Detector summary": alert.summary if alert.summary is not None else NOT_PROVIDED,
        "Forecaster": _model(alert.models.forecaster),
        "Detector": _model(alert.models.detector),
        "Audit reference": _audit(alert.audit),
        "Recorded by server": _recorded(alert.created_at),
    }
    sensors = tuple(
        {
            "Sensor": s.sensor,
            "Deviation (m)": str(s.deviation_m),
            "z": _num(s.z),
            "Since": fmt_time(s.since),
        }
        for s in alert.deviating_sensors
    )
    contributions = (
        None
        if alert.forecast_contributions_m is None
        else tuple(
            {"Sensor": sensor, "Feature": feature, "Contribution (m)": str(value)}
            for sensor, by_feature in alert.forecast_contributions_m.items()
            for feature, value in by_feature.items()
        )
    )
    return AlertDetail(
        alert_id=alert.alert_id,
        simulated=alert.is_mock,
        facts=tuple({"Evidence": k, "Value": v} for k, v in facts.items()),
        sensors=sensors,
        contributions=contributions,
    )
