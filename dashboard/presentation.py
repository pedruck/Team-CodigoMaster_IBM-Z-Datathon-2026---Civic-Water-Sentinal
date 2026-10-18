"""Presentation only: escaped contract values, local assets, and source-derived diagrams."""

import base64
import math
from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st

from dashboard.alert_list import fmt_time
from dashboard.header import Header
from dashboard.source import AlertSource
from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import StatusResponse

ASSETS = Path(__file__).with_name("assets")
STAGES = (
    ("validation", "Validation"),
    ("temporal_policy", "Temporal policy"),
    ("history", "History buffer"),
    ("forecast", "Forecast"),
    ("detection", "Detection"),
    ("audit", "Audit"),
)


@lru_cache
def icon(name: str) -> str:
    """Names are application constants, never values supplied by the source."""
    svg = (ASSETS / "icons" / f"{name}.svg").read_text(encoding="utf-8")
    svg = svg.replace('fill="currentColor"', 'fill="#657770"')
    return (
        '<img class="cw-icon" aria-hidden="true" alt="" '
        f'src="data:image/svg+xml;base64,{_svg_base64(svg)}">'
    )


def _svg_base64(svg: str) -> str:
    # st.html permits HTML only; SVG images retain the standard HTML sanitisation.
    return base64.b64encode(svg.encode("utf-8")).decode("ascii")


@lru_cache
def _styles() -> str:
    faces = []
    for family, filename, weight in (
        ("IBM Plex Sans", "IBMPlexSans-Regular.woff2", "400"),
        ("IBM Plex Sans", "IBMPlexSans-Medium.woff2", "500"),
        ("IBM Plex Sans", "IBMPlexSans-SemiBold.woff2", "600"),
        ("IBM Plex Sans", "IBMPlexSans-Bold.woff2", "700"),
        ("IBM Plex Mono", "IBMPlexMono-Regular.woff2", "400"),
    ):
        encoded = base64.b64encode((ASSETS / "fonts" / filename).read_bytes()).decode("ascii")
        faces.append(
            f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};"
            f"font-display:swap;src:url(data:font/woff2;base64,{encoded}) format('woff2');}}"
        )
    return "<style>" + "".join(faces) + (ASSETS / "theme.css").read_text("utf-8") + "</style>"


def apply_theme() -> None:
    st.html(_styles())


def render_sidebar(source: AlertSource, header: Header, refresh_s: float) -> None:
    with st.sidebar:
        st.html(
            f'<div class="brand"><span class="brand-mark">{icon("drop")}</span>'
            "<div>CivicWater<span>SENTINEL</span></div></div>"
        )
        st.html(
            '<div class="workspace"><span class="workspace-avatar">LT</span>'
            "<div><strong>L-Town network</strong><small>Simulation workspace</small></div></div>"
        )
        links = (
            ("network-alerts", "squares-four", "Network & alerts"),
            ("alert-detail", "fingerprint", "Alert evidence"),
            ("history", "clock-counter-clockwise", "History"),
            ("system-status", "gauge", "System status"),
        )
        st.html(
            '<nav class="workspace-nav" aria-label="Workspace"><span>MONITORING</span>'
            + "".join(
                f'<a href="#{anchor}" target="_self"'
                f"{' class=selected aria-current=location' if index == 0 else ''}>"
                f"{icon(glyph)}<span>{label}</span></a>"
                for index, (anchor, glyph, label) in enumerate(links)
            )
            + "</nav>"
        )
        st.page_link("pages/network_map_page.py", label="Open full network map")
        mode = (
            "Recorded replay"
            if source.recording
            else "API connected"
            if header.online
            else "Offline"
        )
        state = "recorded" if source.recording else "online" if header.online else "offline"
        st.html(
            '<div class="source-card">'
            f'<strong><span class="state-dot {state}"></span>{mode}</strong>'
            f"<small>Refresh interval · {refresh_s:g} seconds</small></div>"
        )
        with st.expander("Source & models"):
            st.metric(
                "API",
                "recorded replay" if source.recording else "online" if header.online else "offline",
            )
            st.metric("Simulated time", header.simulated_time)
            st.metric("Forecaster", header.forecaster)
            st.metric("Detector", header.detector)
            if source.recording and source.recording.note:
                st.caption(source.recording.note)
        st.html(
            '<div class="sidebar-footer"><span class="team-avatar">CM</span>'
            "<div>Team CodigoMaster<small>IBM Z Datathon 2026</small></div></div>"
        )


def render_topbar(source: AlertSource, header: Header) -> None:
    # A recording says so in the notice strip below; the chip only names a live source's state.
    mode = None if source.recording else "CONNECTED" if header.online else "OFFLINE"
    chip = f'<span class="mode-label">{icon("broadcast")}{mode}</span>' if mode else ""
    with st.container(key="title-row", horizontal=True, vertical_alignment="center"):
        st.title("Network & alerts", anchor="network-alerts")
        st.html(
            f'<p class="title-meta">{chip}'
            f"<span>Latest simulated window <strong>{escape(header.simulated_time)}</strong></span>"
            "</p>"
        )


def _metric(label: str, value: str, note: str, glyph: str) -> str:
    return (
        f'<article class="stat-card" data-sentinel-reveal><div class="stat-label">'
        f'{escape(label)}<span class="stat-icon">{icon(glyph)}</span></div>'
        f"<strong>{escape(value)}</strong><small>{escape(note)}</small></article>"
    )


def render_metrics(status: StatusResponse | None) -> None:
    accepted = status.counters.get("windows_accepted") if status else None
    alerts = status.counters.get("alerts_stored") if status else None
    sensors = status.sensor_registry.get("pressure") if status else None
    latency = status.latency.get("total") if status else None
    p95 = latency.p95_ms if latency else None
    cards = (
        (
            "Pressure sensors",
            str(sensors) if sensors is not None else "—",
            "Registry count · pressure",
            "waveform",
        ),
        (
            "Accepted windows",
            f"{accepted:,}" if accepted is not None else "—",
            "Cumulative in this run",
            "squares-four",
        ),
        (
            "Stored alerts",
            f"{alerts:,}" if alerts is not None else "—",
            "Recent records · not active cases",
            "pulse",
        ),
        (
            "Pipeline p95",
            f"{p95:.2f} ms" if p95 is not None else "—",
            "Source-reported processing time",
            "gauge",
        ),
    )
    st.html(
        '<section class="stats-grid" aria-label="Run metrics">'
        + "".join(_metric(*card) for card in cards)
        + "</section>"
    )


def processing_profile(status: StatusResponse | None) -> str:
    """No time series is available: show reported stage p95s, never a fabricated trend."""
    values = {
        key: summary.p95_ms
        for key, _ in STAGES
        if status
        and (summary := status.latency.get(key)) is not None
        and summary.p95_ms is not None
        and math.isfinite(summary.p95_ms)
    }
    maximum = max((value for value in values.values() if value > 0), default=1.0)
    rows = []
    for key, label in STAGES:
        value = values.get(key)
        amount = f"{value:.3f} ms" if value is not None else "not provided"
        fraction = max(0.0, min(1.0, value / maximum)) if value is not None else 0.0
        rows.append(
            f'<div class="latency-row"><span>{label}</span><div class="latency-track">'
            f'<span class="latency-bar {"emphasis" if key == "detection" else ""}" '
            f'style="transform:scaleX({fraction:.6f})"></span></div><output>{amount}</output></div>'
        )
    return (
        '<article class="profile-card" data-sentinel-reveal><div class="panel-heading">'
        "<div><h3>Processing profile</h3><p>Time spent at each stage of the pipeline.</p></div>"
        '<span class="quiet-tag">p95 · milliseconds</span></div>'
        '<div class="latency-chart" role="group" aria-label="Source-reported p95 latency by stage">'
        + "".join(rows)
        + '</div><div class="panel-footnote">'
        "Cumulative stage statistics from this run. Bar lengths share one scale.</div></article>"
    )


def buffer_profile(status: StatusResponse | None) -> str:
    history = status.history if status else None
    fraction = (
        min(1.0, max(0.0, history.contiguous / history.capacity))
        if history and history.capacity > 0
        else 0.0
    )
    current = str(history.contiguous) if history else "—"
    capacity = str(history.capacity) if history else "—"
    required = str(history.required) if history else "—"
    ready = history is not None and history.contiguous >= history.required
    label = "Warm-up met" if ready else "Warming up" if history else "Awaiting source"
    ring = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" '
        'fill="none" stroke-width="5">'
        '<circle stroke="#edf2ef" cx="60" cy="60" r="52"/>'
        '<circle stroke="#08796f" stroke-linecap="round" cx="60" cy="60" r="52" '
        f'pathLength="100" stroke-dasharray="{fraction * 100:.3f} 100"/></svg>'
    )
    return (
        '<article class="buffer-card" data-sentinel-reveal><div class="panel-heading">'
        "<div><h3>Window buffer</h3><p>Contiguous history available.</p></div>"
        f"{icon('database')}</div>"
        '<div class="buffer-body"><div class="buffer-ring">'
        f'<img src="data:image/svg+xml;base64,{_svg_base64(ring)}" '
        f'alt="{current} of {capacity} contiguous windows">'
        f"<div><strong>{current}</strong><span>of {capacity}</span></div></div>"
        f'<div class="buffer-copy"><span class="quiet-tag {"ready" if ready else ""}">'
        f"{label}</span>"
        f"<p>{required} windows required<br>before scoring can begin.</p></div></div>"
        '<div class="panel-footnote">In-memory history · cleared on restart</div></article>'
    )


def render_profiles(status: StatusResponse | None) -> None:
    st.html(
        '<section class="profiles-grid" aria-label="Processing status">'
        + processing_profile(status)
        + buffer_profile(status)
        + "</section>"
    )


def _signed(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}".replace("-", "\u2212")


def _score_bar(alert: Alert) -> str:
    """The detector's own numbers on one scale: the alert exists because the score passed
    the threshold. Nothing is computed beyond positioning the two received values."""
    if alert.cusum is None or alert.cusum.threshold <= 0 or alert.cusum.statistic < 0:
        return ""
    stat, limit = alert.cusum.statistic, alert.cusum.threshold
    scale = max(stat, limit) * 1.2
    return (
        f'<div class="score-bar" role="img" aria-label="Detection score {escape(str(stat))} '
        f'against threshold {escape(str(limit))}">'
        f'<span class="fill" style="width:{stat / scale * 100:.2f}%"></span>'
        f'<span class="limit" style="left:{limit / scale * 100:.2f}%"></span></div>'
        '<p class="score-note">The alert was raised when the score passed the threshold.</p>'
    )


def map_heading(alert: Alert | None) -> str:
    """One line above the map naming what it shows; on phones it is the first thing read."""
    if alert is None:
        context = "Whole network · no alert selected"
    else:
        pipe = (
            f"candidate pipe {alert.candidate_pipe} (best guess)"
            if alert.candidate_pipe
            else "candidate pipe not provided"
        )
        context = (
            f"{alert.region or 'Region not available'} · {fmt_time(alert.window_end)} · {pipe}"
        )
    return (
        '<div class="map-heading"><h2 id="network-map">Network map</h2>'
        f"<p>{escape(context)}</p></div>"
    )


def evidence_pane(alert: Alert, explanation: str) -> str:
    """The selected alert's key evidence beside the map. Values as received, rounded only for
    reading; the exact values stay in the full record below."""
    region = escape(alert.region or "Region not available")
    badge = '<span class="alert-badge">SIMULATED</span>' if alert.is_mock else ""
    score = escape(str(alert.cusum.statistic)) if alert.cusum else "not provided"
    threshold = escape(str(alert.cusum.threshold)) if alert.cusum else "not provided"
    pipe = (
        f"<dd><code>{escape(alert.candidate_pipe)}</code> <small>best guess</small></dd>"
        if alert.candidate_pipe
        else "<dd>not provided</dd>"
    )
    maximum = max((abs(d.deviation_m) for d in alert.deviating_sensors), default=0.0) or 1.0
    rows = "".join(
        f'<li><code>{escape(d.sensor)}</code><span class="bar-track">'
        f'<span class="bar" style="transform:scaleX({min(1.0, abs(d.deviation_m) / maximum):.4f})">'
        f"</span></span><output>{_signed(d.deviation_m)} m</output></li>"
        for d in alert.deviating_sensors
    )
    sensors = (
        f'<ol class="evidence-sensors" aria-label="Observed minus expected pressure">{rows}</ol>'
        if rows
        else '<p class="muted">No deviating sensor.</p>'
    )
    return (
        '<section class="evidence" aria-labelledby="evidence-title">'
        f'<div class="evidence-head"><h2 id="evidence-title">{region}</h2>{badge}</div>'
        f'<code class="evidence-id">{escape(alert.alert_id)}</code>'
        '<dl class="evidence-score">'
        f"<div><dt>Score (CUSUM)</dt><dd>{score}</dd></div>"
        f"<div><dt>Threshold</dt><dd>{threshold}</dd></div></dl>"
        f"{_score_bar(alert)}"
        '<dl class="evidence-facts">'
        f"<div><dt>Window end</dt><dd>{escape(fmt_time(alert.window_end))}</dd></div>"
        f"<div><dt>Estimated onset</dt><dd>{escape(fmt_time(alert.suspected_onset))}</dd></div>"
        f"<div><dt>Candidate pipe</dt>{pipe}</div></dl>"
        f"<h3>Deviating sensors</h3>{sensors}"
        f'<h3>Explanation</h3><p class="explanation-slot">{escape(explanation)}</p>'
        '<a class="evidence-more" href="#alert-detail" target="_self">'
        "Full record and audit reference</a></section>"
    )


def sensor_plot(alert: Alert) -> str:
    """Bars encode received deviations; the raw precision remains in the evidence table."""
    if not alert.deviating_sensors:
        return '<p class="chart-empty">No deviating sensor.</p>'
    maximum = (
        max((abs(sensor.deviation_m) for sensor in alert.deviating_sensors), default=0.0) or 1.0
    )
    rows = []
    for sensor in alert.deviating_sensors:
        width = min(1.0, abs(sensor.deviation_m) / maximum)
        direction = "negative" if sensor.deviation_m < 0 else "positive"
        rows.append(
            f'<div class="sensor-row"><code>{escape(sensor.sensor)}</code>'
            '<div class="deviation-track"><span class="zero-line"></span>'
            f'<span class="deviation-bar {direction}" style="transform:scaleX({width:.6f})">'
            f'<span class="sr-only">{sensor.deviation_m} m</span></span></div>'
            f"<output>{sensor.deviation_m:+.3f} m</output></div>"
        )
    return (
        '<div class="sensor-plot" role="group" '
        'aria-label="Observed minus expected pressure, in metres">'
        '<div class="deviation-legend"><span>Below expected</span><span>Above expected</span></div>'
        + "".join(rows)
        + "<p>Observed - expected · metres of pressure head</p></div>"
    )


def render_motion(*, scroll_to_detail: bool = False) -> None:
    """Only trusted, static local scripts execute; source values never enter JavaScript."""
    vendor = base64.b64encode((ASSETS / "vendor" / "gsap.min.js").read_bytes()).decode("ascii")
    motion = (ASSETS / "motion.js").read_text("utf-8")
    # DOMPurify strips inline library code containing markup literals. Load the trusted
    # local distribution as script text; neither eval nor a remote script is needed.
    loader = (
        "if (!window.gsap) { const library = document.createElement('script');"
        f"library.textContent = atob('{vendor}');"
        "document.head.appendChild(library); library.remove(); }"
    )
    navigation = (
        "requestAnimationFrame(() => { const heading = document.getElementById('alert-detail');"
        "if (heading) { heading.setAttribute('tabindex', '-1');"
        "heading.focus({preventScroll:true}); heading.scrollIntoView({block:'start'}); } });"
        if scroll_to_detail
        else ""
    )
    st.html(
        f'<span hidden aria-hidden="true"></span><script>{loader}\n{motion}\n{navigation}</script>',
        unsafe_allow_javascript=True,
    )
