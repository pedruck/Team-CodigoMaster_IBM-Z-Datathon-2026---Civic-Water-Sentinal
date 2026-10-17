"""Streamlit entry point: ``just dashboard``. Rendering only; no business rules here."""

import streamlit as st

from dashboard.config import source_from_env
from dashboard.header import build_header
from dashboard.source import ContractMismatchError, SourceError, SourceUnavailableError
from sentinel.schemas.api import StatusResponse

st.set_page_config(page_title="CivicWater Sentinel", layout="wide")

source = source_from_env()
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
if header.simulated:
    st.warning("SIMULATED — mock forecaster/detector active: not leak detection.")
if header.provisional_sensors and status is not None:
    st.warning("Provisional sensor IDs in use.")
st.caption("Simulated data (BattLeDIM L-Town). Not validated on a real network.")

cols = st.columns(4)
cols[0].metric("API", "online" if header.online else "offline")
cols[1].metric("Simulated time", header.simulated_time)
cols[2].metric("Forecaster", header.forecaster)
cols[3].metric("Detector", header.detector)
