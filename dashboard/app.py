"""Streamlit entry point: ``just dashboard``. Rendering only; no business rules here (ADR-009).

First viewport (ADR-012 addendum): the alert queue, the interactive network map and the selected
alert's key evidence side by side. The full record, history and system status follow.
"""

import streamlit as st

from dashboard.alert_detail import AlertDetail, alert_detail
from dashboard.config import refresh_interval_s, source_from_env
from dashboard.header import build_header
from dashboard.history import render_history
from dashboard.live import render_alert_queue, render_all_fields, render_recent_alerts, shown_alerts
from dashboard.map_embed import map_document
from dashboard.network_map import load_map
from dashboard.presentation import (
    apply_theme,
    evidence_pane,
    icon,
    map_heading,
    render_metrics,
    render_motion,
    render_profiles,
    render_sidebar,
    render_topbar,
    sensor_plot,
)
from dashboard.source import (
    AlertSource,
    ContractMismatchError,
    SourceError,
    SourceUnavailableError,
)
from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import ExplanationStatus, StatusResponse

MAP_HEIGHT = 560  # the stylesheet resizes the frame to the viewport


def refresh_alerts() -> None:
    """A manual refresh also resets the poll's back-off timer."""
    st.session_state.pop("recent_alerts_poll", None)


def load_selected(source: AlertSource, selected: Alert | None) -> tuple[Alert, AlertDetail] | None:
    """The selected record as the source serves it; errors are shown, never papered over."""
    if selected is None:
        return None
    try:
        alert = source.get_alert(selected.alert_id)
    except ContractMismatchError as exc:
        st.error(f"Contract mismatch: alert {selected.alert_id} does not match the contract. {exc}")
        return None
    except SourceError as exc:
        st.error(f"Could not load alert {selected.alert_id} ({source.name}): {exc}")
        return None
    return alert, alert_detail(alert, load_explanation(source, alert.alert_id))


def load_explanation(source: AlertSource, alert_id: str) -> ExplanationStatus | None:
    """The stored explanation (Q-AUD-07). It is optional: a failure is reported, and the
    structured evidence is still shown."""
    try:
        return source.get_explanation(alert_id)
    except SourceError as exc:
        st.caption(f"Explanation of {alert_id} could not be loaded ({source.name}): {exc}")
        return None


def render_map(alert: Alert | None) -> None:
    """The interactive map; the static SVG stays available on the map page."""
    st.html(map_heading(alert))
    try:
        document = map_document(load_map(), alert, shown_alerts())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.warning(f"Network map unavailable: {exc}")
        return
    st.iframe(document, height=MAP_HEIGHT)


def render_detail(loaded: tuple[Alert, AlertDetail] | None) -> None:
    st.header("Alert detail", anchor="alert-detail")
    if loaded is None:
        st.caption("No alert to show.")
        return
    alert, detail = loaded
    if detail.simulated:
        st.warning("SIMULATED — this alert came from a mock: not leak detection.")
    st.caption(f"{alert.alert_id} · values exactly as received from the source.")
    chart, record = st.columns([1, 1.25], gap="large")
    with chart:
        st.markdown("**Deviating sensors**")
        st.html(sensor_plot(alert))
        if detail.sensors:
            with st.expander("Sensor readings · exact values"):
                st.table(list(detail.sensors))
        else:
            st.caption("No deviating sensor.")
    with record:
        with st.expander("Full record & audit reference"):
            st.table(list(detail.facts))
        with st.expander("Forecast contributions & explanation"):
            st.markdown("**Forecast contributions (deviating sensors)**")
            if detail.contributions:
                st.table(list(detail.contributions))
            else:
                st.caption("Forecast contributions: not provided.")
            st.markdown("**Explanation**")
            st.info(detail.explanation)
            if detail.explanation_note:
                st.caption(detail.explanation_note)


st.set_page_config(
    page_title="CivicWater Sentinel", layout="wide", initial_sidebar_state="collapsed"
)
apply_theme()

try:
    source = source_from_env()
    refresh_s = refresh_interval_s()
except (SourceError, ValueError) as exc:
    st.error(f"Cannot start the dashboard: {exc}")
    st.stop()
status: StatusResponse | None = None
online = False
try:
    source.health()
    online = True
    status = source.status()
except ContractMismatchError as exc:
    st.error(f"Contract mismatch: the API answer does not match the contract. {exc}")
except SourceUnavailableError as exc:
    st.error(f"API unavailable ({source.name}): {exc}")
except SourceError as exc:
    st.error(f"API error ({source.name}): {exc}")

header = build_header(status, online)
render_sidebar(source, header, refresh_s)
with st.container(key="sentinel-shell"):
    render_topbar(source, header)
    with st.container(key="data-notices"):
        if source.recording is not None:
            rec = source.recording
            notices = [f"RECORDED REPLAY — run {rec.run_id}, {rec.recorded_at}. Not live data."]
        else:
            notices = []
        if header.simulated:
            notices.append("SIMULATED — mock model or non-final calibration: not leak detection.")
        if header.provisional_sensors and status is not None:
            notices.append("Provisional sensor IDs in use.")
        # A snapshot's age, or why a recorded run replaced the live source (BE-026).
        if notice := getattr(source, "notice", None):
            notices.insert(0, notice)
        if notices:
            st.warning("  ·  ".join(notices))
        # The fixed caption leads; a recording's own description (e.g. "SYNTHETIC, hand-authored
        # ...") follows it on the same line.
        caption = "Simulated data (BattLeDIM L-Town). Not validated on a real network."
        if source.recording is not None and source.recording.note:
            caption += f" · Recording note: {source.recording.note}"
        st.caption(caption)
    loaded: tuple[Alert, AlertDetail] | None = None
    with st.container(key="workspace"):
        queue_pane, map_pane, evidence_pane_col = st.columns([220, 760, 270], gap="small")
        with queue_pane, st.container(key="queue-pane"):
            with st.container(key="recent-heading", horizontal=True, vertical_alignment="center"):
                st.header("Recent alerts", anchor="recent-alerts")
                st.button("Refresh", on_click=refresh_alerts, key="refresh_alerts")
            render_recent_alerts(source, refresh_s)
            selected = render_alert_queue()
        loaded = load_selected(source, selected)
        with map_pane, st.container(key="map-pane"):
            render_map(loaded[0] if loaded else None)
        with evidence_pane_col, st.container(key="evidence-pane"):
            if loaded is not None:
                alert, detail = loaded
                st.html(evidence_pane(alert, detail.explanation, detail.explanation_note))
            elif status is None:
                st.html(
                    '<p class="pane-empty">The alert source is not reachable, so no evidence '
                    "can be shown. The map still draws the network.</p>"
                )
            elif not shown_alerts():
                st.html(
                    '<p class="pane-empty">No alert selected. When an alert is issued, its '
                    "region, sensors and candidate pipe appear here and on the map.</p>"
                )
    with st.container(key="alert-panel"):
        render_detail(loaded)
        render_all_fields()
    if status is not None:
        with st.container(key="history-panel"):
            render_history(source, status.last_timestamp)
    with st.container(key="status-panel"):
        st.header("System status", anchor="system-status")
        with st.expander("Sensors, processing and history buffer"):
            render_metrics(status)
            render_profiles(status)
    st.html(
        '<footer class="page-footer"><span>CivicWater Sentinel · Team CodigoMaster</span>'
        '<span>Network: L-Town, <a href="https://doi.org/10.5281/zenodo.4017659">BattLeDIM 2020'
        "</a> (Vrachimis et al.), CC BY 4.0</span>"
        f"<span>{icon('database')}In-memory state · read-only workspace</span></footer>"
    )
render_motion(scroll_to_detail=bool(st.session_state.pop("scroll_to_alert_detail", False)))
