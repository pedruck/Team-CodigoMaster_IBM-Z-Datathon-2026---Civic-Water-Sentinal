"""Jupyter view of the dashboard (ipywidgets), for JupyterLab on the LinuxONE VM (ADR-009).

Streamlit cannot run in the VM's Jupyter container (no ``pyarrow`` wheel for s390x, no port for
it), so this view renders the same data with ipywidgets: same ``AlertSource`` seam, same poll
(``dashboard.refresh``: ``since`` + dedup, back-off), same formatting (``alert_list``,
``alert_detail``, ``header``) and the same mandatory banners. Rendering only; no rules here.

    from dashboard.jupyter import JupyterDashboard
    dash = JupyterDashboard(HttpAlertSource("http://127.0.0.1:8000")); dash.start(); dash.widget
"""

import threading
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import escape

import ipywidgets as widgets

from dashboard.alert_detail import alert_detail
from dashboard.alert_list import alert_row
from dashboard.header import build_header
from dashboard.refresh import PollState, backoff_s, poll
from dashboard.source import AlertSource, SourceError

WORDING = "Simulated data (BattLeDIM L-Town). Not validated on a real network."
_BANNER = "padding:6px 10px;margin:2px 0;border-radius:4px;background:{bg};color:{fg}"
WARN = _BANNER.format(bg="#fff4ce", fg="#5c4400")
ERROR = _BANNER.format(bg="#fde7e9", fg="#8a1022")


def _box(text: str, style: str) -> str:
    return f'<div style="{style}">{escape(text)}</div>'


def _table(rows: Sequence[Mapping[str, object]]) -> str:
    if not rows:
        return ""
    head = "".join(f"<th>{escape(str(c))}</th>" for c in rows[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row.values()) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


class JupyterDashboard:
    def __init__(self, source: AlertSource, refresh_s: float = 5.0) -> None:
        self.source = source
        self.refresh_s = refresh_s
        self.state = PollState()
        self.banners = widgets.HTML()
        self.header = widgets.HTML()
        self.alerts = widgets.HTML()
        self.select = widgets.Dropdown(description="Alert", options=())
        self.detail = widgets.HTML()
        self.select.observe(lambda _: self._render_detail(), names="value")
        self.widget = widgets.VBox(
            [
                self.banners,
                self.header,
                widgets.HTML("<h3>Recent alerts</h3>"),
                self.alerts,
                widgets.HTML("<h3>Alert detail</h3>"),
                self.select,
                self.detail,
            ]
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def refresh(self, wall_now: datetime | None = None, mono_now: float | None = None) -> None:
        """One poll and a re-render. Clocks are injectable for tests."""
        self.state = poll(
            self.state,
            self.source,
            self.refresh_s,
            wall_now or datetime.now(UTC),
            time.monotonic() if mono_now is None else mono_now,
        )
        self._render()

    def start(self) -> None:
        """Refresh now, then every ``refresh_s`` seconds in a background thread."""
        self.refresh()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.refresh_s + 1)

    def _loop(self) -> None:
        while not self._stop.wait(self.refresh_s):
            self.refresh()

    # --- rendering ---------------------------------------------------------------------------

    def _render(self) -> None:
        state = self.state
        status = state.status
        recording = self.source.recording
        online = state.loaded and state.error_kind != "unavailable"
        head = build_header(status, online)

        banners = []
        if recording is not None:
            banners.append(
                _box(
                    f"RECORDED REPLAY — run {recording.run_id}, {recording.recorded_at}. "
                    f"Not live data. {recording.note}".strip(),
                    WARN,
                )
            )
        if head.simulated:
            banners.append(
                _box(
                    "SIMULATED — a mock model or a non-final calibration is active "
                    "(is_mock): not leak detection.",
                    WARN,
                )
            )
        if head.provisional_sensors and status is not None:
            banners.append(_box("Provisional sensor IDs in use.", WARN))
        banners.append(f"<small>{escape(WORDING)}</small>")
        self.banners.value = "".join(banners)

        api = "recorded replay" if recording is not None else ("online" if online else "offline")
        self.header.value = _table(
            [
                {
                    "API": api,
                    "Simulated time": head.simulated_time,
                    "Forecaster": head.forecaster,
                    "Detector": head.detector,
                }
            ]
        )

        parts = []
        if state.error_kind == "unavailable" and state.unavailable_since is not None:
            parts.append(
                _box(
                    f"API unavailable since {state.unavailable_since:%H:%M} UTC (wall clock) — "
                    f"retrying in {backoff_s(state.failures, self.refresh_s):g} s. "
                    f"Data below may be stale. ({self.source.name}: {state.error})",
                    ERROR,
                )
            )
        elif state.error_kind == "mismatch":
            parts.append(
                _box(f"Contract mismatch: alerts do not match the contract. {state.error}", ERROR)
            )
        elif state.error_kind == "error":
            parts.append(_box(f"Could not load alerts ({self.source.name}): {state.error}", ERROR))
        if state.alerts:
            now = status.last_timestamp if status else None
            parts.append(_table([alert_row(a, now) for a in state.alerts]))
        elif state.loaded and state.error_kind is None:
            parts.append("<p>No alerts in this period.</p>")
        self.alerts.value = "".join(parts)

        options = tuple(a.alert_id for a in state.alerts)
        if self.select.options != options:
            keep = self.select.value if self.select.value in options else None
            self.select.options = options
            self.select.value = keep or (options[0] if options else None)
        self._render_detail()

    def _render_detail(self) -> None:
        selected = self.select.value
        if not selected:
            self.detail.value = "<p>No alert to show.</p>"
            return
        try:
            alert = self.source.get_alert(selected)
        except SourceError as exc:
            self.detail.value = _box(f"Could not load alert {selected}: {exc}", ERROR)
            return
        try:
            explanation = self.source.get_explanation(selected)
        except SourceError:
            explanation = None  # optional: the structured evidence below is still shown
        detail = alert_detail(alert, explanation)
        parts = []
        if detail.simulated:
            parts.append(_box("SIMULATED — this alert came from a mock: not leak detection.", WARN))
        parts.append(_table(detail.facts))
        parts.append("<b>Deviating sensors</b>")
        parts.append(_table(detail.sensors) or "<p>No deviating sensor.</p>")
        parts.append("<b>Forecast contributions (deviating sensors)</b>")
        parts.append(
            _table(detail.contributions or ()) or "<p>Forecast contributions: not provided.</p>"
        )
        parts.append(f"<b>Explanation</b><p>{escape(detail.explanation)}</p>")
        if detail.explanation_note:
            parts.append(f"<p><small>{escape(detail.explanation_note)}</small></p>")
        self.detail.value = "".join(parts)
