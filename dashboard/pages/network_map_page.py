"""Streamlit page "Network map" (multipage: ``dashboard/pages/``). Rendering only (ADR-009)."""

import streamlit as st

from dashboard.config import source_from_env
from dashboard.map_embed import map_image
from dashboard.network_map import load_map, map_svg
from dashboard.presentation import apply_theme
from dashboard.source import AlertQuery, SourceError

st.set_page_config(page_title="Network map · CivicWater Sentinel", layout="wide")
apply_theme()
with st.sidebar:
    st.html('<a class="map-back-link" href="/" target="_self">Back to issued alerts</a>')
st.title("Network map")
st.caption("Simulated data (BattLeDIM L-Town). Not validated on a real network.")

try:
    net = load_map()
except (OSError, ValueError, KeyError, TypeError) as exc:
    st.error(f"Network map unavailable: {exc}")
    st.stop()
source = source_from_env()
if source.recording is not None:
    st.warning("RECORDED REPLAY — not live data.")
try:
    alerts = sorted(
        source.list_alerts(AlertQuery(limit=500)).items, key=lambda a: (a.window_end, a.alert_id)
    )
except SourceError as exc:
    st.error(f"Could not load alerts ({source.name}): {exc}")
    alerts = []

alert = None
if alerts:
    ids = [a.alert_id for a in alerts]
    chosen = (
        st.select_slider("Alert (time order)", options=ids, value=ids[-1], key="map_alert")
        if len(ids) > 1
        else ids[0]
    )
    alert = next(a for a in alerts if a.alert_id == chosen)
    if len(ids) == 1:
        st.caption(f"Showing the only recorded alert: {chosen}")
    if alert.is_mock:
        st.warning("SIMULATED — this alert came from a mock: not leak detection.")
else:
    st.caption("No alert to show: drawing the network only.")

st.html(map_image(net, alert))
st.caption(
    "When available, the highlighted region, deviating sensors and candidate pipe come from "
    "the selected alert. The candidate is a best guess, not a confirmed leak. "
    "Scroll inside the map on narrow screens. "
    "[Network source: BattLeDIM, CC BY 4.0](https://doi.org/10.5281/zenodo.4017659)."
)
st.download_button(
    "Download SVG",
    map_svg(net, alert, width=1200),
    file_name="l-town-network.svg",
    mime="image/svg+xml",
)
