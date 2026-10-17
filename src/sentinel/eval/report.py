"""Markdown report of one layer-2 run (ADR-010 §5, §9). Pure: evaluation + manifest → text.

Every report carries the wording of ADR-008 §H. A run with a mock model, provisional sensor IDs
or uncommitted code is watermarked NOT A RESULT and must not be cited.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sentinel.eval.metrics import OPERATOR_MAX_DELAY, Evaluation
from sentinel.schemas.common import ModelInfo

WORDING = (
    "> simulated benchmark (BattLeDIM L-Town); thresholds fixed on 2018; 2019 run once. "
    "Simulated data, not validated on a real network."
)
PROTOCOL = "ADR-010 v1 (layer-2 evaluation)"
OPERATOR_DAYS = OPERATOR_MAX_DELAY.days


@dataclass(frozen=True, slots=True)
class Manifest:
    """What makes the run reproducible (ADR-010 §8)."""

    run_id: str
    commit: str
    dirty: bool
    forecaster: ModelInfo
    detector: ModelInfo
    registry_provisional: bool
    registry_sha256: str
    lock_sha256: str
    calibration_id: str | None
    data_sha256: dict[str, str]
    architecture: str
    python: str

    @property
    def watermark_reasons(self) -> list[str]:
        reasons = []
        if self.forecaster.is_mock or self.detector.is_mock:
            reasons.append("a mock model or a non-final calibration (is_mock)")
        if self.registry_provisional:
            reasons.append("provisional sensor IDs")
        if self.dirty:  # ADR-008 §G: the run comes from an identified commit
            reasons.append("uncommitted code")
        return reasons


def _time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "missed"


def _span(value: timedelta | None) -> str:
    return str(value) if value is not None else "not provided"


def _model(info: ModelInfo) -> str:
    return f"{info.name} {info.version}{' (mock)' if info.is_mock else ''}"


def render_report(evaluation: Evaluation, manifest: Manifest) -> str:
    lines = [f"# Layer-2 evaluation — run {manifest.run_id}", ""]
    if manifest.watermark_reasons:
        lines += [f"**NOT A RESULT** — {'; '.join(manifest.watermark_reasons)}. Do not cite.", ""]
    lines += [WORDING, ""]

    primary, operator, new = evaluation.primary, evaluation.operator, evaluation.new_in_year
    lines += [
        "## Detection (primary: official BattLeDIM time window, all labelled leaks)",
        "",
        f"Detected: {primary.detected}/{primary.total}. Repeats: {primary.repeat_openings}. "
        f"False alarms under this window: {primary.false_alarms} "
        f"(0 by construction when a leak is active all year; see the {OPERATOR_DAYS}-day view). "
        "Hits are **time-only**: the alert's region or pipe is not checked against the leak.",
        "",
        "Leaks active before the year started (counted): "
        f"{', '.join(evaluation.inherited) or 'none'}.",
        "",
    ]
    for kind, count in primary.by_type.items():
        missed = ", ".join(count.missed) or "none"
        lines.append(f"- {kind}: {count.detected}/{count.total} (missed: {missed})")
    lines += [
        "",
        f"Delay from start (nearest rank): p50 {_span(evaluation.delay_p50)}, "
        f"p90 {_span(evaluation.delay_p90)}, max {_span(evaluation.delay_max)}. "
        f"Delay from peak: p50 {_span(evaluation.peak_delay_p50)}, "
        f"max {_span(evaluation.peak_delay_max)}.",
        "",
        "| Pipe | Type | Start | Detected at | Delay from start | Delay from peak | Onset error |",
        "|---|---|---|---|---|---|---|",
    ]
    for leak in primary.leaks:
        if leak.detected_at is None:
            lines.append(f"| {leak.pipe} | {leak.type} | {_time(leak.start)} | missed | | | |")
            continue
        lines.append(
            f"| {leak.pipe} | {leak.type} | {_time(leak.start)} | {_time(leak.detected_at)} | "
            f"{_span(leak.delay_from_start)} | {_span(leak.delay_from_peak)} | "
            f"{_span(evaluation.onset_error.get(leak.pipe))} |"
        )
    lines += [
        "",
        f"## Operator view (detected within {OPERATOR_DAYS} days of start)",
        "",
        f"Detected: {operator.detected}/{operator.total}. "
        f"False alarms ({OPERATOR_DAYS}-day view): {operator.false_alarms} over "
        f"{operator.weeks:.1f} weeks = {evaluation.false_alarms_per_week:.2f} per week. "
        f"Repeats: {operator.repeat_openings}.",
        "",
        "False alarm times: " + (", ".join(_time(t) for t in operator.false_alarm_times) or "none"),
        "",
        "## Leaks new in the year (primary rule)",
        "",
        f"Detected: {new.detected}/{new.total} (false alarms: see the operator view).",
        "",
        "## Not computed",
        "",
        "- Region accuracy: not computed (needs the L-Town sensor → region mapping and a pipe → "
        "region table, ADR-010 §6).",
        "- BattLeDIM score: not computed (needs a candidate pipe per alert, Q-DET-07).",
        "",
        "## Manifest",
        "",
        f"- Protocol: {PROTOCOL}",
        f"- Commit: {manifest.commit}{' (dirty)' if manifest.dirty else ''}",
        f"- Forecaster: {_model(manifest.forecaster)}",
        f"- Detector: {_model(manifest.detector)}",
        f"- Calibration: {manifest.calibration_id or 'not provided'}",
        f"- Sensor registry: {'provisional' if manifest.registry_provisional else 'official'}, "
        f"sha256 {manifest.registry_sha256}",
        f"- uv.lock: sha256 {manifest.lock_sha256}",
        f"- Platform: {manifest.architecture}, Python {manifest.python}",
    ]
    lines += [f"- {name}: sha256 {digest}" for name, digest in sorted(manifest.data_sha256.items())]
    return "\n".join(lines) + "\n"
