"""Streamlit entry point: ``just dashboard``. Rendering only; no business rules here."""

import streamlit as st

from dashboard.alert_detail import alert_detail
from dashboard.alert_list import alert_row
from dashboard.config import source_from_env
from dashboard.header import build_header
from dashboard.source import (
    AlertQuery,
    ContractMismatchError,
    SourceError,
    SourceUnavailableError,
)
from sentinel.schemas.api import StatusResponse

st.set_page_config(page_title="CivicWater Sentinel", layout="wide")

try:
    source = source_from_env()
except SourceError as exc:
    st.error(f"Cannot open the data source: {exc}")
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

st.title("CivicWater Sentinel")
if source.recording is not None:
    rec = source.recording
    st.warning(
        f"RECORDED REPLAY — run {rec.run_id}, {rec.recorded_at}. Not live data. {rec.note}".strip()
    )
if header.simulated:
    st.warning("SIMULATED — mock forecaster/detector active: not leak detection.")
if header.provisional_sensors and status is not None:
    st.warning("Provisional sensor IDs in use.")
st.caption("Simulated data (BattLeDIM L-Town). Not validated on a real network.")

cols = st.columns(4)
if source.recording is not None:
    cols[0].metric("API", "recorded replay")
else:
    cols[0].metric("API", "online" if header.online else "offline")
cols[1].metric("Simulated time", header.simulated_time)
cols[2].metric("Forecaster", header.forecaster)
cols[3].metric("Detector", header.detector)

st.subheader("Recent alerts")
st.caption(
    "Newest first. Alerts are never closed by the detector yet: this is not an 'active' list."
)
if status is not None:
    try:
        with st.spinner("Loading alerts…"):
            page = source.list_alerts(AlertQuery())
    except ContractMismatchError as exc:
        st.error(f"Contract mismatch: alerts do not match the contract. {exc}")
    except SourceError as exc:
        st.error(f"Could not load alerts ({source.name}): {exc}")
    else:
        if page.items:
            now = status.last_timestamp
            st.dataframe([alert_row(a, now) for a in page.items], hide_index=True, width="stretch")
            st.caption(f"{len(page.items)} of {page.total} alerts.")
        else:
            st.info("No alerts in this period.")

# --- Alert detail (UI-004): evidence exactly as received; the list above owns its errors. ---
st.subheader("Alert detail")
if status is not None:
    try:
        shown = source.list_alerts(AlertQuery()).items
    except SourceError:
        shown = ()
    selected = (
        st.selectbox("Alert", [a.alert_id for a in shown], index=0, key="alert_detail_id")
        if shown
        else None
    )
    if selected is None:
        st.caption("No alert to show.")
    else:
        try:
            detail = alert_detail(source.get_alert(selected))
        except ContractMismatchError as exc:
            st.error(f"Contract mismatch: alert {selected} does not match the contract. {exc}")
        except SourceError as exc:
            st.error(f"Could not load alert {selected} ({source.name}): {exc}")
        else:
            if detail.simulated:
                st.warning("SIMULATED — this alert came from a mock: not leak detection.")
            st.table(list(detail.facts))
            st.markdown("**Deviating sensors**")
            if detail.sensors:
                st.table(list(detail.sensors))
            else:
                st.caption("No deviating sensor.")
            st.markdown("**Forecast contributions (deviating sensors)**")
            if detail.contributions:
                st.table(list(detail.contributions))
            else:
                st.caption("Forecast contributions: not provided.")
            st.markdown("**Explanation**")
            st.info(detail.explanation)
