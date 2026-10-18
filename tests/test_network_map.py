"""Network map (SVG) for the dashboard: ``dashboard.network_map``, the Jupyter view and the
Streamlit page. Small invented map for exact answers; the real ``config/l-town-map.json`` only
for smoke tests."""

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from dashboard.map_embed import map_image
from dashboard.network_map import DEFAULT_MAP, REGION, load_map, map_svg, parse_map
from dashboard.network_map_view import NETWORK_ONLY, NetworkMapView
from dashboard.source import PlaybackAlertSource
from sentinel.schemas.alerts import Alert

ROOT = Path(__file__).resolve().parents[1]
CUSUM = ROOT / "dashboard" / "playback" / "fixture-cusum"

MINI: dict[str, Any] = {
    "schema_version": "1.0",
    "attribution": "test map",
    "counts": {"nodes": 3, "pipes": 2, "pressure_sensors": 2},
    "bbox": [0, 0, 100, 50],
    "links": [
        {
            "id": "p1",
            "type": "pipe",
            "x1": 0,
            "y1": 0,
            "x2": 50,
            "y2": 0,
            "diameter": 100,
            "sensor": "n1",
            "region": "RA",
        },
        {
            "id": "p2",
            "type": "pipe",
            "x1": 50,
            "y1": 0,
            "x2": 100,
            "y2": 50,
            "diameter": None,
            "sensor": "n2",
            "region": "RB",
        },
    ],
    "sensors": [
        {"id": "n1", "x": 0, "y": 0, "region": "RA"},
        {"id": "n2", "x": 100, "y": 50, "region": "RB"},
    ],
    "landmarks": [{"id": "T1", "type": "tank", "x": 50, "y": 0}],
}


def alert(**changes: Any) -> Alert:
    raw = json.loads((CUSUM / "alerts.jsonl").read_text().splitlines()[0])
    raw.update(
        schema_version="1.1",
        region="RB",
        candidate_pipe="p2",
        forecast_contributions_m=None,
        deviating_sensors=[
            {"sensor": "n2", "deviation_m": -0.83, "z": 3.1, "since": raw["window_end"]}
        ],
    )
    raw.update(changes)
    return Alert.model_validate(raw)


def test_network_only() -> None:
    svg = map_svg(parse_map(MINI))
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count("<line ") >= 2
    assert "3 junctions · 2 pipes · 2 pressure sensors" in svg
    assert "Suspected leak" not in svg
    assert "SIMULATED" not in svg
    assert "test map" in svg


def test_alert_region_sensor_and_candidate_pipe() -> None:
    a = alert()
    svg = map_svg(parse_map(MINI), a)
    assert f"Alert {a.alert_id}" in svg
    assert "region RB" in svg
    assert "Suspected leak (best guess): p2" in svg
    assert "n2 -0.83 m" in svg
    assert svg.count(f'stroke="{REGION}"') == 2  # p2 (the alert's region) + the legend
    assert ("SIMULATED" in svg) == a.is_mock


def test_unknown_candidate_pipe_and_region_are_reported_not_drawn() -> None:
    svg = map_svg(parse_map(MINI), alert(region="R-OTHER", candidate_pipe="<p9>"))
    assert "Suspected leak" not in svg
    assert "region R-OTHER is not in the map" in svg
    assert "candidate pipe &lt;p9&gt; is not in the map" in svg
    assert "<p9>" not in svg
    assert svg.count(f'stroke="{REGION}"') == 1  # the legend only


def test_other_schema_version_is_refused() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        parse_map({**MINI, "schema_version": "9.9"})


def test_streamlit_image_preserves_svg_and_alt_context() -> None:
    record = alert()
    html = map_image(parse_map(MINI), record)
    encoded = html.split("base64,", 1)[1].split('"', 1)[0]
    svg = base64.b64decode(encoded).decode("utf-8")
    assert html.startswith('<div class="network-map-scroll" role="region" tabindex="0"')
    assert f"Alert {record.alert_id}, region RB" in html
    assert "Suspected leak (best guess): p2" in svg


@pytest.mark.skipif(not DEFAULT_MAP.exists(), reason="config/l-town-map.json not generated")
def test_real_map_renders() -> None:
    net = load_map()
    assert sum(1 for lk in net.links if lk.type == "pipe") == 905
    svg = map_svg(net, alert(region="Z1-R3", candidate_pipe=net.links[0].id))
    assert "785 junctions" not in svg  # an alert replaces the subtitle
    assert "Suspected leak (best guess)" in svg
    assert any(link.id == "p4" and link.region == "Z1-R3" for link in net.links)


@pytest.mark.skipif(not DEFAULT_MAP.exists(), reason="config/l-town-map.json not generated")
def test_jupyter_view_walks_the_alerts(tmp_path: Path) -> None:
    view = NetworkMapView(PlaybackAlertSource(CUSUM))
    view.refresh()
    assert view.slider.options[0] == NETWORK_ONLY
    assert len(view.slider.options) == len(view.alerts) + 1
    view.slider.value = NETWORK_ONLY
    assert "Suspected leak" not in view.map.value
    out = view.save_svg(tmp_path / "map.svg")
    assert out.read_text(encoding="utf-8").startswith("<svg")


@pytest.mark.skipif(not DEFAULT_MAP.exists(), reason="config/l-town-map.json not generated")
def test_streamlit_page_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(CUSUM))
    app = testing.AppTest.from_file("../dashboard/pages/network_map_page.py").run()
    assert not app.exception
    assert app.title[0].value == "Network map"
