"""The auto-refreshing 'Recent alerts' queue. Rules live in ``dashboard.refresh``; this module
only keeps the poll state and renders it (ADR-009).

The poll runs in a Streamlit fragment and reports its own state (count, errors); a change in
the list reruns the whole page. The queue itself is an ordinary widget of the page, so picking
an alert reruns the page and the map and the evidence follow the selection.
"""

import time
from datetime import UTC, datetime

import streamlit as st

from dashboard.alert_list import alert_row, fmt_time
from dashboard.refresh import PollState, backoff_s, needs_full_rerun, poll
from dashboard.source import AlertSource
from sentinel.schemas.alerts import Alert

STATE_KEY = "recent_alerts_poll"
SELECT_KEY = "alert_detail_id"


def shown_alerts() -> tuple[Alert, ...]:
    """The alerts the list currently shows (empty before the first load or on a mismatch)."""
    state: PollState = st.session_state.get(STATE_KEY, PollState())
    return state.alerts


def poll_state() -> PollState:
    state: PollState = st.session_state.get(STATE_KEY, PollState())
    return state


def render_recent_alerts(source: AlertSource, refresh_s: float) -> None:
    """Polls now and every ``refresh_s`` seconds; shows the count and any failure."""
    st.fragment(_recent_alerts, run_every=refresh_s)(source, refresh_s)


def _recent_alerts(source: AlertSource, refresh_s: float) -> None:
    previous: PollState = st.session_state.get(STATE_KEY, PollState())
    with st.spinner("Loading alerts…"):
        state = poll(previous, source, refresh_s, datetime.now(UTC), time.monotonic())
    st.session_state[STATE_KEY] = state
    if needs_full_rerun(previous, state):
        st.rerun()  # header clock, queue, map and evidence follow the list

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
        count = len(state.alerts)
        st.html(
            '<p class="feed-summary">'
            f'<strong role="status" aria-atomic="true">'
            f"{count} recent {'alert' if count == 1 else 'alerts'}</strong>"
            "<span>newest first · UTC</span></p>"
        )
        if state.truncated:
            st.warning("More new alerts arrived than one request returns: see History below.")
    else:
        st.info("No alerts in this period. The map shows the whole network.")


def queue_label(alert: Alert) -> str:
    """Two short lines per alert; the full record stays in the evidence and the table."""
    strongest = max(alert.deviating_sensors, key=lambda d: abs(d.deviation_m), default=None)
    count = len(alert.deviating_sensors)
    region = alert.region or "Region not available"
    second = f"{count} {'sensor' if count == 1 else 'sensors'}"
    if strongest is not None:  # the largest deviation; its sensor is on the map and beside it
        second += f" · {signed_m(strongest.deviation_m)}"
    # Markdown label: escape the characters that would change its meaning.
    return f"**{_md(region)}** · {fmt_time(alert.window_end)[11:16]}  \n{_md(second)}"


def signed_m(value: float) -> str:
    """Signed metres with a true minus sign, as on the map."""
    return f"{value:+.2f} m".replace("-", "\u2212")


def _md(text: str) -> str:
    return "".join(f"\\{c}" if c in "\\`*_{}[]<>()#+-.!|~" else c for c in text)


def render_alert_queue() -> Alert | None:
    """The queue of recent alerts; returns the selected one (the newest by default)."""
    alerts = shown_alerts()
    if not alerts:
        return None
    by_id = {a.alert_id: a for a in alerts}
    if st.session_state.get(SELECT_KEY) not in by_id:
        st.session_state[SELECT_KEY] = alerts[0].alert_id
    labels = {alert_id: queue_label(a) for alert_id, a in by_id.items()}
    repeated = {label for label in labels.values() if list(labels.values()).count(label) > 1}
    for alert_id, label in labels.items():
        if label in repeated:  # identical summaries: the ID tells the rows apart
            labels[alert_id] = f"{label}  \n{_md(alert_id)}"
    with st.container(key="alert-queue"):
        selected = st.radio(
            "Select an alert to show it on the map",
            list(by_id),
            format_func=labels.__getitem__,
            key=SELECT_KEY,
            label_visibility="collapsed",
        )
    return by_id.get(selected)


def render_all_fields() -> None:
    """Every loaded record and column, under the fold."""
    state = poll_state()
    if not state.alerts:
        return
    now = state.status.last_timestamp if state.status else None
    with st.expander(f"All alert fields · {len(state.alerts)} loaded records"):
        st.caption("Issued records do not have an open or closed status.")
        st.dataframe(
            [alert_row(a, now) for a in state.alerts],
            hide_index=True,
            width="stretch",
            row_height=48,
        )
