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
    alert_preview,
    buffer_profile,
    processing_profile,
    render_alert_snapshot,
    sensor_plot,
)
from dashboard.source import AlertQuery, PlaybackAlertSource
from tests.test_dashboard_refresh import fixture_alert, write_run

RUN = Path(__file__).resolve().parents[1] / "dashboard" / "playback" / "fixture-cusum"
EDGE = RUN.with_name("fixture-edge-cases")


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


def test_preview_explains_the_record_without_inventing_severity_or_lifecycle() -> None:
    source = PlaybackAlertSource(RUN)
    (alert,) = source.list_alerts(AlertQuery()).items

    html = alert_preview(alert, source.status().last_timestamp)

    assert "R-SYN-A" in html
    assert "Issued 2019-03-14 17:05 UTC" in html
    assert "<strong>3</strong> deviating sensors" in html
    assert "-0.205 m" in html
    assert "6 h 50 min" in html
    assert "SIMULATED" in html
    assert "critical" not in html.lower()
    assert "active" not in html.lower()


def test_preview_handles_absent_region_and_sensors_and_escapes_received_strings() -> None:
    (alert,) = PlaybackAlertSource(RUN).list_alerts(AlertQuery()).items
    missing = alert.model_copy(update={"region": None, "deviating_sensors": (), "is_mock": False})
    assert "Region not available" in alert_preview(missing, None)
    assert "No deviating sensor reported" in alert_preview(missing, None)
    assert "unknown" in alert_preview(missing, None)
    assert "SIMULATED" not in alert_preview(missing, None)
    untrusted = alert.model_copy(update={"region": '<img src=x onerror="x()">'})
    assert "&lt;img" in alert_preview(untrusted, None)
    assert "<img" not in alert_preview(untrusted, None)


def test_issued_alerts_come_before_technical_metrics_and_keep_the_full_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(RUN))

    app: Any = testing.AppTest.from_file("../dashboard/app.py").run()

    assert not app.exception
    assert app.title[0].value == "Issued alerts"
    assert [heading.value for heading in app.header][:3] == [
        "Recent alerts",
        "Alert detail",
        "History",
    ]
    html = "".join(element.proto.body for element in app.get("html"))
    assert html.index('class="alert-preview"') < html.index('class="stats-grid"')
    assert 'role="status" aria-atomic="true"' in html
    assert "Age (simulated)" in app.dataframe[0].value.columns
    assert len(app.dataframe[0].value) == 1
    expanders = {expander.label: expander for expander in app.expander}
    assert "All alert fields" in expanders
    assert not expanders["System status · sensors, processing and history buffer"].proto.expanded


def test_view_evidence_selects_the_clicked_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(EDGE))
    app: Any = testing.AppTest.from_file("../dashboard/app.py").run()
    assert not app.exception
    assert app.selectbox(key="alert_detail_id").value == "A-20190314T1640Z-93"

    app.button(key="inspect_alert_A-20190314T1640Z-1").click().run()

    assert not app.exception
    assert app.selectbox(key="alert_detail_id").value == "A-20190314T1640Z-1"
    facts = next(table.value for table in app.table if "Evidence" in table.value.columns)
    assert facts.loc[facts["Evidence"] == "Alert", "Value"].iloc[0] == "A-20190314T1640Z-1"


def test_compact_feed_keeps_all_loaded_records_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    ids = {f"A-preview-{index}" for index in range(7)}
    run = write_run(
        tmp_path / "run",
        [fixture_alert(alert_id, "2019-03-14T16:40:00Z") for alert_id in sorted(ids)],
    )
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(run))

    app: Any = testing.AppTest.from_file("../dashboard/app.py").run()

    assert not app.exception
    previews = [
        element for element in app.get("html") if 'class="alert-preview"' in element.proto.body
    ]
    assert len(previews) == 5
    assert set(app.dataframe[0].value["Alert"]) == ids
    assert set(app.selectbox(key="alert_detail_id").options) == ids
    assert any("Showing 5 of 7" in caption.value for caption in app.caption)
