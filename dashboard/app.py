"""Streamlit entry point: ``just dashboard``. Rendering only; no business rules here (ADR-009)."""

import streamlit as st

from dashboard.alert_detail import alert_detail
from dashboard.config import refresh_interval_s, source_from_env
from dashboard.header import build_header
from dashboard.history import render_history
from dashboard.live import render_recent_alerts, shown_alerts
from dashboard.presentation import (
    apply_theme,
    icon,
    render_alert_snapshot,
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
from sentinel.schemas.api import StatusResponse


def refresh_alerts() -> None:
    """A manual refresh also resets the poll's back-off timer."""
    st.session_state.pop("recent_alerts_poll", None)


def render_detail(source: AlertSource, status: StatusResponse | None) -> None:
    st.header("Alert detail", anchor="alert-detail")
    st.caption("Inspect the evidence behind a record. Values come directly from the source.")
    if status is None:
        return
    shown = shown_alerts()
    selected = (
        st.selectbox("Alert", [a.alert_id for a in shown], index=0, key="alert_detail_id")
        if shown
        else None
    )
    if selected is None:
        st.caption("No alert to show.")
        return
    try:
        alert = source.get_alert(selected)
        detail = alert_detail(alert)
    except ContractMismatchError as exc:
        st.error(f"Contract mismatch: alert {selected} does not match the contract. {exc}")
        return
    except SourceError as exc:
        st.error(f"Could not load alert {selected} ({source.name}): {exc}")
        return
    if detail.simulated:
        st.warning("SIMULATED — this alert came from a mock: not leak detection.")
    snapshot, evidence = st.columns([1, 1.25], gap="large")
    with snapshot:
        render_alert_snapshot(alert)
    with evidence:
        st.markdown("**Deviating sensors**")
        st.html(sensor_plot(alert))
        if detail.sensors:
            with st.expander("Sensor readings · exact values"):
                st.table(list(detail.sensors))
        else:
            st.caption("No deviating sensor.")
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


st.set_page_config(page_title="CivicWater Sentinel", layout="wide", initial_sidebar_state="auto")
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
            notices.append(
                "SIMULATED — a mock model or a non-final calibration is active (is_mock): "
                "not leak detection."
            )
        if header.provisional_sensors and status is not None:
            notices.append("Provisional sensor IDs in use.")
        if notices:
            st.warning("\n\n".join(notices))
        st.caption("Simulated data (BattLeDIM L-Town). Not validated on a real network.")
    with st.container(key="recent-panel"):
        heading, controls = st.columns([3, 1], vertical_alignment="center")
        heading.header("Recent alerts", anchor="recent-alerts")
        with controls, st.container(key="refresh-controls", horizontal=True):
            st.button("Refresh data", on_click=refresh_alerts, key="refresh_alerts")
        render_recent_alerts(source, refresh_s)
    with st.container(key="alert-panel"):
        render_detail(source, status)
    if status is not None:
        with st.container(key="history-panel"):
            render_history(source, status.last_timestamp)
    with st.expander("System status · sensors, processing and history buffer"):
        st.header("System status", anchor="system-status")
        render_metrics(status)
        render_profiles(status)
    st.html(
        '<footer class="page-footer"><span>CivicWater Sentinel · Team CodigoMaster</span>'
        f"<span>{icon('database')}In-memory state · read-only workspace</span></footer>"
    )
render_motion(scroll_to_detail=bool(st.session_state.pop("scroll_to_alert_detail", False)))
