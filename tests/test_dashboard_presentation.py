"""Source-derived diagrams, safe markup and the operator's manual refresh (BE-021)."""

import base64
import math
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("streamlit")

from dashboard.presentation import (
    buffer_profile,
    processing_profile,
    render_alert_snapshot,
    sensor_plot,
)
from dashboard.source import AlertQuery, PlaybackAlertSource

RUN = Path(__file__).resolve().parents[1] / "dashboard" / "playback" / "fixture-cusum"


def test_stage_chart_uses_the_reported_p95s_without_a_time_series() -> None:
    html = processing_profile(PlaybackAlertSource(RUN).status())

    assert "0.243 ms" in html  # validation p95 0.2426, rounded for display
    assert "0.755 ms" in html  # detection p95 0.755
    assert "0.091 ms" in html  # audit p95 0.0914
    assert html.index("Validation") < html.index("Forecast") < html.index("Detection")
    assert "transform:scaleX(1.000000)" in html
    assert "Cumulative stage statistics" in html


def test_missing_stage_statistics_are_not_displayed_as_zero() -> None:
    assert processing_profile(None).count("not provided") == 6
    status = PlaybackAlertSource(RUN).status().model_copy(update={"latency": {}})
    assert processing_profile(status).count("not provided") == 6


def test_buffer_shows_the_contiguous_count_and_backend_requirement() -> None:
    html = buffer_profile(PlaybackAlertSource(RUN).status())

    assert 'alt="288 of 288 contiguous windows"' in html
    assert "144 windows required" in html
    assert "Warm-up met" in html
    assert "cleared on restart" in html


def test_unknown_buffer_does_not_claim_readiness_and_zero_capacity_is_finite() -> None:
    assert "Awaiting source" in buffer_profile(None)
    status = PlaybackAlertSource(RUN).status()
    history = status.history.model_copy(update={"capacity": 0, "contiguous": 0})
    html = buffer_profile(status.model_copy(update={"history": history}))

    assert "Warming up" in html
    image = re.search(r'buffer-ring"><img src="data:image/svg\+xml;base64,([^\"]+)', html)
    assert image is not None
    svg = base64.b64decode(image[1]).decode()
    assert 'stroke-dasharray="0.000 100"' in svg


def test_sensor_chart_preserves_sign_zero_and_exact_received_values() -> None:
    (alert,) = PlaybackAlertSource(RUN).list_alerts(AlertQuery()).items
    sensors = (
        alert.deviating_sensors[0].model_copy(update={"deviation_m": -0.20540000000000447}),
        alert.deviating_sensors[1].model_copy(update={"deviation_m": 0.1}),
        alert.deviating_sensors[2].model_copy(update={"deviation_m": 0.0}),
    )
    html = sensor_plot(alert.model_copy(update={"deviating_sensors": sensors}))

    assert "-0.20540000000000447 m" in html  # accessible text retains raw precision
    assert "-0.205 m" in html
    assert "+0.100 m" in html
    assert "+0.000 m" in html
    assert html.count('class="deviation-bar negative"') == 1
    assert html.count('class="deviation-bar positive"') == 2
    assert "scaleX(0.000000)" in html
    assert "No deviating sensor" in sensor_plot(alert.model_copy(update={"deviating_sensors": ()}))


def test_source_strings_are_escaped_before_entering_html(monkeypatch: pytest.MonkeyPatch) -> None:
    (alert,) = PlaybackAlertSource(RUN).list_alerts(AlertQuery()).items
    sensor = alert.deviating_sensors[0].model_copy(update={"sensor": '<img src=x onerror="x()">'})
    html = sensor_plot(alert.model_copy(update={"deviating_sensors": (sensor,)}))
    assert "<img" not in html
    assert "&lt;img" in html
    rendered: list[str] = []
    monkeypatch.setattr("dashboard.presentation.st.html", rendered.append)

    render_alert_snapshot(alert.model_copy(update={"region": "<script>alert(1)</script>"}))

    assert "<script>" not in rendered[0]
    assert "&lt;script&gt;" in rendered[0]


def test_manual_refresh_recovers_without_waiting_for_the_old_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(RUN))
    app: Any = testing.AppTest.from_file("../dashboard/app.py").run()
    assert not app.exception
    state = app.session_state["recent_alerts_poll"]
    app.session_state["recent_alerts_poll"] = replace(
        state, failures=3, retry_at=math.inf, error="interrupted", error_kind="unavailable"
    )

    app.button(key="refresh_alerts").click().run()

    assert not app.exception
    assert app.session_state["recent_alerts_poll"].failures == 0
    assert app.session_state["recent_alerts_poll"].error is None
    assert len(app.dataframe[0].value) == 1


def test_offline_metrics_remain_unknown_instead_of_inventing_zero_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.delenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", raising=False)
    monkeypatch.setenv("SENTINEL_DASHBOARD_API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SENTINEL_DASHBOARD_TIMEOUT_S", "1")

    app: Any = testing.AppTest.from_file("../dashboard/app.py").run()

    assert not app.exception
    metrics = next(
        element.proto.body
        for element in app.get("html")
        if 'class="stats-grid"' in element.proto.body
    )
    assert metrics.count("<strong>—</strong>") == 4
    assert any("API unavailable" in error.value for error in app.error)
