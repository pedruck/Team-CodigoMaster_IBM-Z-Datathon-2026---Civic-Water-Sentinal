"""The auto-refreshing 'Recent alerts' list (Streamlit fragment). Rules live in
``dashboard.refresh``; this module only keeps the poll state and renders it (ADR-009)."""

import time
from datetime import UTC, datetime

import streamlit as st

from dashboard.alert_list import alert_row
from dashboard.presentation import alert_preview
from dashboard.refresh import PollState, backoff_s, needs_full_rerun, poll
from dashboard.source import AlertSource
from sentinel.schemas.alerts import Alert

STATE_KEY = "recent_alerts_poll"
PREVIEW_LIMIT = 5


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
        count = len(state.alerts)
        st.html(
            '<div class="feed-summary">'
            f'<strong role="status" aria-atomic="true">'
            f"{count} recent {'alert' if count == 1 else 'alerts'}</strong>"
            "<span>Newest first · Times are UTC</span></div>"
        )
        for index, alert in enumerate(state.alerts[:PREVIEW_LIMIT]):
            with st.container(key=f"issued-{index}"):
                summary, action = st.columns([4, 1.25], vertical_alignment="center")
                with summary:
                    st.html(alert_preview(alert, now))
                if action.button("View evidence", key=f"inspect_alert_{alert.alert_id}"):
                    st.session_state["alert_detail_id"] = alert.alert_id
                    st.session_state["scroll_to_alert_detail"] = True
                    st.rerun()
        if count > PREVIEW_LIMIT:
            st.caption(
                f"Showing {PREVIEW_LIMIT} of {count} recent records. "
                "Expand 'All alert fields' to see every loaded record."
            )
        with st.expander("All alert fields"):
            st.caption("Issued records do not have an open or closed status.")
            st.dataframe(
                [alert_row(a, now) for a in state.alerts],
                hide_index=True,
                width="stretch",
                row_height=48,
            )
        st.caption(f"Updates every {refresh_s:g} seconds. More records in History.")
        if state.truncated:
            st.warning("More new alerts arrived than one request returns: see the history below.")
    else:
        st.info("No alerts in this period.")
