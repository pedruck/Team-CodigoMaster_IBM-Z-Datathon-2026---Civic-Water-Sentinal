"""Jupyter (ipywidgets) view of the dashboard: the same source, poll and formatting as the
Streamlit page, rendered as widgets for JupyterLab on the VM (no Streamlit/pyarrow needed).

Expected values are literals read off the shipped playback fixtures.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("ipywidgets")

from dashboard.jupyter import JupyterDashboard
from dashboard.source import HttpAlertSource, PlaybackAlertSource

PLAYBACK = Path(__file__).resolve().parents[1] / "dashboard" / "playback"
WALL = datetime(2026, 10, 17, 22, 0, tzinfo=UTC)


def dashboard(run: str) -> JupyterDashboard:
    dash = JupyterDashboard(PlaybackAlertSource(PLAYBACK / run), refresh_s=5.0)
    dash.refresh(wall_now=WALL, mono_now=0.0)
    return dash


def test_cusum_run_shows_banners_header_list_and_detail() -> None:
    dash = dashboard("fixture-cusum")

    assert "RECORDED REPLAY — run fixture-cusum, 2026-10-17" in dash.banners.value
    assert "SIMULATED" in dash.banners.value
    assert "Not validated on a real network" in dash.banners.value
    assert "2019-03-14 23:55 UTC" in dash.header.value
    assert "cusum 0.1.0+036e55e6 (mock)" in dash.header.value
    assert "recorded replay" in dash.header.value
    for text in ("A-20190314T1705Z-1", "R-SYN-A", "2019-03-14 16:35 UTC", "6 h 50 min"):
        assert text in dash.alerts.value
    assert dash.select.options == ("A-20190314T1705Z-1",)
    assert "9.393 vs threshold 8.0" in dash.detail.value
    assert "explanation unavailable" in dash.detail.value
    assert "verif" not in dash.detail.value.lower()


def test_selecting_another_alert_shows_its_evidence() -> None:
    dash = dashboard("fixture-edge-cases")

    dash.select.value = "A-20190314T1640Z-91"

    assert "audit unavailable" in dash.detail.value
    assert "region not available" in dash.detail.value
    dash.select.value = "A-20190314T1640Z-93"
    assert "R-FIXTURE" in dash.detail.value


def test_empty_run_shows_the_empty_state() -> None:
    dash = dashboard("fixture-empty")

    assert "No alerts in this period" in dash.alerts.value
    assert dash.select.options == ()
    assert "No alert to show" in dash.detail.value


def test_malformed_run_shows_contract_mismatch_and_no_data() -> None:
    dash = dashboard("fixture-malformed")

    assert "Contract mismatch" in dash.alerts.value
    assert "A-2019" not in dash.alerts.value
    assert dash.select.options == ()


def test_unreachable_api_shows_unavailable_since_and_backs_off() -> None:
    dash = JupyterDashboard(HttpAlertSource("http://127.0.0.1:9", timeout_s=1), refresh_s=5.0)

    dash.refresh(wall_now=WALL, mono_now=0.0)

    assert "API unavailable since 22:00 UTC" in dash.alerts.value
    assert "retrying in 10 s" in dash.alerts.value
    assert "offline" in dash.header.value


def test_values_from_the_api_are_html_escaped(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    source = PLAYBACK / "fixture-cusum"
    alert = json.loads((source / "alerts.jsonl").read_text())
    alert["summary"] = "<script>alert(1)</script>"
    alert["region"] = "<b>R</b>"
    (run / "alerts.jsonl").write_text(json.dumps(alert) + "\n")
    (run / "status.json").write_text((source / "status.json").read_text())

    dash = JupyterDashboard(PlaybackAlertSource(run), refresh_s=5.0)
    dash.refresh(wall_now=WALL, mono_now=0.0)

    assert "<script>" not in dash.detail.value
    assert "&lt;script&gt;" in dash.detail.value
    assert "<b>R</b>" not in dash.alerts.value


def test_start_and_stop_the_background_refresh() -> None:
    dash = JupyterDashboard(PlaybackAlertSource(PLAYBACK / "fixture-cusum"), refresh_s=5.0)

    dash.start()
    dash.stop()

    assert dash.running is False
    assert "A-20190314T1705Z-1" in dash.alerts.value  # start() refreshes once immediately
