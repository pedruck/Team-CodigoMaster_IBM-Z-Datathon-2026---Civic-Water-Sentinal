"""Structured, deterministic explanation of an alert. Narrative owner (P5).

``alert_facts`` is the only thing a language model ever sees, and ``render_text`` is the
fallback text when no model is available or its output is rejected (design document §6).
Neither decides anything: they restate what the detector put in the alert.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from sentinel.schemas.alerts import Alert

Language = Literal["pt", "en"]
DEFAULT_LANGUAGE: Language = "en"  # team decision; "pt" stays available

TOP_CONTRIBUTIONS = 3


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def _model(name: str, version: str) -> str:
    return f"{name}@{version}"


def top_contributions(alert: Alert, n: int = TOP_CONTRIBUTIONS) -> dict[str, list[dict[str, Any]]]:
    """Largest forecaster contributions (|metres|) per deviating sensor, in alert order."""
    if not alert.forecast_contributions_m:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for d in alert.deviating_sensors:
        features = alert.forecast_contributions_m.get(d.sensor)
        if features:
            ranked = sorted(features.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[:n]
            out[d.sensor] = [{"feature": f, "value_m": v} for f, v in ranked]
    return out


def alert_facts(alert: Alert) -> dict[str, Any]:
    """JSON-ready facts of the alert, without anything the detector did not produce."""
    return {
        "alert_id": alert.alert_id,
        "classification": alert.classification,
        "is_mock": alert.is_mock,
        "window_end": _iso(alert.window_end),
        "region": alert.region,
        "suspected_onset": _iso(alert.suspected_onset),
        "hours_since_onset": alert.hours_since_onset,
        "cusum": alert.cusum.model_dump() if alert.cusum else None,
        "deviating_sensors": [
            {
                "sensor": d.sensor,
                "deviation_m": d.deviation_m,
                "z": d.z,
                "since": _iso(d.since),
            }
            for d in alert.deviating_sensors
        ],
        "top_contributions_m": top_contributions(alert) or None,
        "detector_summary": alert.summary,
        "models": {
            "forecaster": _model(alert.models.forecaster.name, alert.models.forecaster.version),
            "detector": _model(alert.models.detector.name, alert.models.detector.version),
        },
        "audit_seq": alert.audit.seq if alert.audit else None,
    }


# ------------------------------------------------------------------------------- template

_TEXT: dict[Language, dict[str, str]] = {
    "pt": {
        "mock": "[SIMULAÇÃO] Gerado com componentes mock: não é uma detecção real de vazamento.",
        "head": "Alerta {id}: suspeita de vazamento",
        "region": " na região {region}.",
        "no_region": " (região não estimada).",
        "window": "Janela avaliada: {date} {time} UTC.",
        "onset": "Início estimado do desvio: {date} {time} UTC",
        "hours": ", {hours} h antes da janela.",
        "sensors": "Sensores com desvio (observado - esperado): {items}.",
        "sensor": "{sensor} {dev} m",
        "z": " (z {z})",
        "cusum": "CUSUM {stat} para limiar {thr}.",
        "contrib": "Principais contribuições do modelo de previsão: {items}.",
        "models": "Modelos: previsão {f}; detecção {d}.",
        "audit": "Registro de auditoria seq {seq}.",
        "no_audit": "Alerta sem registro de auditoria.",
        "scope": "Esta explicação descreve a decisão do motor de detecção; ela não decide se há "
        "vazamento.",
    },
    "en": {
        "mock": "[SIMULATION] Produced by mock components: not a real leak detection.",
        "head": "Alert {id}: suspected leak",
        "region": " in region {region}.",
        "no_region": " (region not estimated).",
        "window": "Evaluated window: {date} {time} UTC.",
        "onset": "Estimated deviation onset: {date} {time} UTC",
        "hours": ", {hours} h before the window.",
        "sensors": "Deviating sensors (observed - expected): {items}.",
        "sensor": "{sensor} {dev} m",
        "z": " (z {z})",
        "cusum": "CUSUM {stat} against threshold {thr}.",
        "contrib": "Main forecaster contributions: {items}.",
        "models": "Models: forecaster {f}; detector {d}.",
        "audit": "Audit record seq {seq}.",
        "no_audit": "Alert without an audit record.",
        "scope": "This explanation restates the detection engine's decision; it does not decide "
        "whether a leak exists.",
    },
}


def fmt_number(value: float, lang: Language, decimals: int = 2, signed: bool = False) -> str:
    text = f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"
    return text.replace(".", ",") if lang == "pt" else text


def render_text(alert: Alert, lang: Language = DEFAULT_LANGUAGE) -> str:
    """Deterministic explanation. Every number, ID and time in it comes from the alert."""
    t = _TEXT[lang]
    lines: list[str] = []
    if alert.is_mock:
        lines.append(t["mock"])
    head = t["head"].format(id=alert.alert_id)
    head += t["region"].format(region=alert.region) if alert.region else t["no_region"]
    lines.append(head)
    we = alert.window_end
    lines.append(t["window"].format(date=f"{we:%Y-%m-%d}", time=f"{we:%H:%M}"))
    if alert.suspected_onset:
        on = alert.suspected_onset
        onset = t["onset"].format(date=f"{on:%Y-%m-%d}", time=f"{on:%H:%M}")
        if alert.hours_since_onset is not None:
            onset += t["hours"].format(hours=fmt_number(alert.hours_since_onset, lang))
        else:
            onset += "."
        lines.append(onset)
    items = []
    for d in alert.deviating_sensors:
        item = t["sensor"].format(sensor=d.sensor, dev=fmt_number(d.deviation_m, lang))
        if d.z is not None:
            item += t["z"].format(z=fmt_number(d.z, lang))
        items.append(item)
    lines.append(t["sensors"].format(items="; ".join(items)))
    if alert.cusum:
        lines.append(
            t["cusum"].format(
                stat=fmt_number(alert.cusum.statistic, lang),
                thr=fmt_number(alert.cusum.threshold, lang),
            )
        )
    contributions = top_contributions(alert)
    if contributions:
        parts = [
            f"{sensor}: "
            + ", ".join(
                f"{c['feature']} {fmt_number(c['value_m'], lang, signed=True)} m" for c in feats
            )
            for sensor, feats in contributions.items()
        ]
        lines.append(t["contrib"].format(items="; ".join(parts)))
    lines.append(
        t["models"].format(
            f=_model(alert.models.forecaster.name, alert.models.forecaster.version),
            d=_model(alert.models.detector.name, alert.models.detector.version),
        )
    )
    lines.append(t["audit"].format(seq=alert.audit.seq) if alert.audit else t["no_audit"])
    lines.append(t["scope"])
    return "\n".join(lines)
