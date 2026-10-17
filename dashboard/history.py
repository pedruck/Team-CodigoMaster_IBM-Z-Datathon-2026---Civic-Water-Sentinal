"""Paginated alert history, filtered by time range (UTC) and region (ADR-009).

Same semantics as ``GET /v1/alerts`` (contract §5): newest first, ``since``/``until``
inclusive on ``window_end``, exact ``region``, ``limit``/``offset``. Pagination is
offset-based: alerts stored while the operator pages shift later pages (anchoring on
``until`` is recorded as technical debt).
"""

from datetime import UTC, datetime

import streamlit as st

from dashboard.alert_list import alert_row
from dashboard.source import MAX_LIMIT, AlertQuery, AlertSource, ContractMismatchError, SourceError

DEFAULT_PAGE_SIZE = 25


def parse_utc(text: str) -> datetime | None:
    """Blank → no filter. ISO date/time; without an offset it is read as UTC."""
    text = text.strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a time (use YYYY-MM-DD HH:MM, UTC)") from None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def history_query(
    page: int, page_size: int, since: datetime | None, until: datetime | None, region: str
) -> AlertQuery:
    """Page numbers start at 1."""
    return AlertQuery(
        limit=page_size,
        offset=(page - 1) * page_size,
        since=since,
        until=until,
        region=region.strip() or None,
    )


def page_count(total: int, page_size: int) -> int:
    return max(1, -(-total // page_size))


def render_history(source: AlertSource, now: datetime | None) -> None:
    """Filter widgets and one page of alerts. ``now`` is the simulated clock (for ages)."""
    st.subheader("History")
    st.caption("All stored alerts, newest first. Times are UTC and inclusive, on window end.")
    cols = st.columns(5)
    since_text = cols[0].text_input(
        "Since (UTC)", key="history_since", placeholder="YYYY-MM-DD HH:MM"
    )
    until_text = cols[1].text_input(
        "Until (UTC)", key="history_until", placeholder="YYYY-MM-DD HH:MM"
    )
    region = cols[2].text_input("Region", key="history_region", placeholder="any")
    size = int(
        cols[3].number_input(
            "Page size",
            min_value=1,
            max_value=MAX_LIMIT,
            value=DEFAULT_PAGE_SIZE,
            step=5,
            key="history_page_size",
        )
    )
    page = int(cols[4].number_input("Page", min_value=1, step=1, key="history_page"))
    try:
        query = history_query(page, size, parse_utc(since_text), parse_utc(until_text), region)
    except ValueError as exc:
        st.error(f"Invalid filter: {exc}")
        return
    try:
        result = source.list_alerts(query)
    except ContractMismatchError as exc:
        st.error(f"Contract mismatch: history does not match the contract. {exc}")
        return
    except SourceError as exc:
        st.error(f"Could not load the history ({source.name}): {exc}")
        return
    pages = page_count(result.total, size)
    if result.total == 0:
        st.info("No alerts match the history filters.")
    elif not result.items:
        st.info(f"No alerts on page {page}: the last page is {pages}.")
    else:
        st.dataframe([alert_row(a, now) for a in result.items], hide_index=True, width="stretch")
        first = query.offset + 1
        st.caption(
            f"Page {page} of {pages} — alerts {first} to {query.offset + len(result.items)} "
            f"of {result.total}."
        )
