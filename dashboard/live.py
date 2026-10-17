"""The auto-refreshing 'Recent alerts' list (Streamlit fragment). Rules live in
``dashboard.refresh``; this module only keeps the poll state and renders it (ADR-009)."""

import time
from datetime import UTC, datetime

import streamlit as st

from dashboard.alert_list import alert_row
from dashboard.refresh import PollState, backoff_s, needs_full_rerun, poll
from dashboard.source import AlertSource
from sentinel.schemas.alerts import Alert

STATE_KEY = "recent_alerts_poll"


def shown_alerts() -> tuple[Alert, ...]:
    """The alerts the list currently shows (empty before the first load or on a mismatch)."""
    state: PollState = st.session_state.get(STATE_KEY, PollState())
    return state.alerts


def render_recent_alerts(source: AlertSource, refresh_s: float) -> None:
    """Renders the list now and re-polls every ``refresh_s`` seconds."""
    st.fragment(_recent_alerts, run_every=refresh_s)(source, refresh_s)


def _recent_alerts(source: AlertSource, refresh_s: float) -> None:
    previous: PollState = st.session_state.get(STATE_KEY, PollState())
    with st.spinner("Loading alerts…"):
        state = poll(previous, source, refresh_s, datetime.now(UTC), time.monotonic())
    st.session_state[STATE_KEY] = state
    if needs_full_rerun(previous, state):
        st.rerun()  # header clock and detail selector follow the list

    if state.error_kind == "unavailable" and state.unavailable_since is not None:
        st.error(
            f"API unavailable since {state.unavailable_since:%H:%M} UTC (wall clock) — "
            f"retrying in {backoff_s(state.failures, refresh_s):g} s. "
            f"Data below may be stale. ({source.name}: {state.error})"
        )
    elif state.error_kind == "mismatch":
        st.error(f"Contract mismatch: alerts do not match the contract. {state.error}")
        return  # never render data next to a contract mismatch
    elif state.error_kind == "error":
        st.error(f"Could not load alerts ({source.name}): {state.error}")

    if not state.loaded or (state.error and not state.alerts):
        return
    if state.alerts:
        now = state.status.last_timestamp if state.status else None
        st.dataframe([alert_row(a, now) for a in state.alerts], hide_index=True, width="stretch")
        st.caption(
            f"{len(state.alerts)} most recent alerts, refreshed every {refresh_s:g} s. "
            "Older alerts: see the history below."
        )
        if state.truncated:
            st.warning("More new alerts arrived than one request returns: see the history below.")
    else:
        st.info("No alerts in this period.")
