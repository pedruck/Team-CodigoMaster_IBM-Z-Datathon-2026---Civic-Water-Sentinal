"""Streamlit page "Network map" (multipage: ``dashboard/pages/``). Rendering only (ADR-009).

The same interactive map as the workspace, full width, with the replay of every loaded alert
in time order. The static SVG stays downloadable for slides.
"""

import streamlit as st

from dashboard.config import source_from_env
from dashboard.map_embed import map_document
from dashboard.network_map import load_map, map_svg
from dashboard.presentation import apply_theme
from dashboard.source import AlertQuery, SourceError

st.set_page_config(
    page_title="Network map · CivicWater Sentinel",
    layout="wide",
    initial_sidebar_state="collapsed",
)
apply_theme()
with st.sidebar:
    st.html('<a class="map-back-link" href="/" target="_self">Back to network &amp; alerts</a>')

with st.container(key="sentinel-shell"):
    with st.container(key="title-row", horizontal=True, vertical_alignment="center"):
        st.title("Network map")
    try:
        net = load_map()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f"Network map unavailable: {exc}")
        st.stop()
    source = source_from_env()
    try:
        alerts = sorted(
            source.list_alerts(AlertQuery(limit=500)).items,
            key=lambda a: (a.window_end, a.alert_id),
        )
    except SourceError as exc:
        st.error(f"Could not load alerts ({source.name}): {exc}")
        alerts = []

    with st.container(key="data-notices"):
        notices = []
        if source.recording is not None:
            rec = source.recording
            notices.append(f"RECORDED REPLAY — run {rec.run_id}, {rec.recorded_at}. Not live data.")
        if any(a.is_mock for a in alerts):
            notices.append("SIMULATED — these alerts came from a mock: not leak detection.")
        if notices:
            st.warning("  ·  ".join(notices))
        caption = "Simulated data (BattLeDIM L-Town). Not validated on a real network."
        if source.recording is not None and source.recording.note:
            caption += f" · Recording note: {source.recording.note}"
        st.caption(caption)

    alert = None
    if alerts:
        ids = [a.alert_id for a in alerts]
        chosen = st.session_state.get("map_alert", ids[-1])
        alert = next((a for a in alerts if a.alert_id == chosen), alerts[-1])

    # The map leads; the replay inside it walks every loaded alert in time order.
    with st.container(key="map-page"):
        st.iframe(map_document(net, alert, alerts, variant="page"), height=640)
    if len(alerts) > 1:
        st.select_slider(
            "Open the map on alert (time order)",
            options=[a.alert_id for a in alerts],
            value=alert.alert_id if alert else None,
            key="map_alert",
        )
    elif not alerts:
        st.caption("No alert to show: drawing the network only.")
    st.download_button(
        "Download SVG",
        map_svg(net, alert, width=1200),
        file_name="l-town-network.svg",
        mime="image/svg+xml",
    )
