"""Interactive network map (BE-024): the pure payload (``map_payload``), the self-contained
document (``map_document``), the synthetic demo playback, and both Streamlit pages headless.
The small invented map gives exact answers; the real ``config/l-town-map.json`` only smoke
tests and the demo fixture's IDs."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from dashboard.map_embed import map_document, map_iframe, payload_json
from dashboard.network_map import BACKGROUND, REGION_PALETTE, load_map, map_payload, parse_map
from dashboard.source import AlertQuery, PlaybackAlertSource
from sentinel.schemas.alerts import Alert
from tests.test_network_map import MINI, alert

ROOT = Path(__file__).resolve().parents[1]
PLAYBACK = ROOT / "dashboard" / "playback"
DEMO = PLAYBACK / "fixture-synthetic-ltown-demo"
CUSUM = PLAYBACK / "fixture-cusum"
ASSETS = ROOT / "dashboard" / "assets"
CORAL, ALERT_GREEN = "#ad443a", "#08796f"


def doc_payload(document: str) -> dict[str, Any]:
    match = re.search(r'<script type="application/json" id="map-data">(.*?)</script>', document)
    assert match is not None
    data: dict[str, Any] = json.loads(match[1])
    return data


def test_payload_format_for_an_alert() -> None:
    net = parse_map(MINI)
    payload = map_payload(net, alert(), [alert()])

    assert payload["version"] == "1"
    assert payload["bbox"] == [0.0, 0.0, 100.0, 50.0]
    assert payload["links"][0] == ["p1", "pipe", 0.0, 0.0, 50.0, 0.0, 100.0, "RA", "n1"]
    assert payload["links"][1][6] is None  # unknown diameter stays unknown
    assert payload["sensors"] == [["n1", 0.0, 0.0, "RA"], ["n2", 100.0, 50.0, "RB"]]
    assert payload["landmarks"] == [["T1", "tank", 50.0, 0.0]]
    assert [r["id"] for r in payload["regions"]] == ["RA", "RB"]
    assert payload["regions"][1] == {
        "id": "RB",
        "color": REGION_PALETTE[1],
        "sensors": ["n2"],
        "links": 1,
    }
    (view,) = payload["alerts"]
    assert payload["selected"] == view["id"] == "A-20190314T1705Z-1"
    assert view["region"] == "RB" and view["region_known"] is True
    assert view["candidate_pipe"] == "p2" and view["candidate_known"] is True
    assert view["time_label"] == "2019-03-14 17:05 UTC"
    assert view["sensors"] == [{"id": "n2", "deviation_m": -0.83, "z": 3.1, "known": True}]
    assert view["strongest"] == {"sensor": "n2", "deviation_m": -0.83}
    assert view["is_mock"] is True
    assert view["warnings"] == []
    json.dumps(payload, allow_nan=False)  # JSON-ready as it is


def test_alert_without_region_or_pipe_is_drawn_on_the_network_only() -> None:
    (view,) = map_payload(parse_map(MINI), alert(region=None, candidate_pipe=None))["alerts"]

    assert view["region"] is None and view["region_known"] is False
    assert view["candidate_pipe"] is None and view["candidate_known"] is False
    assert view["warnings"] == []


def test_unknown_region_pipe_and_sensor_are_reported_never_placed() -> None:
    odd = alert(
        region="R-SYN-A",
        candidate_pipe="p999",
        deviating_sensors=[{"sensor": "n77", "deviation_m": -0.4, "z": 2.0, "since": None}],
    )
    (view,) = map_payload(parse_map(MINI), odd)["alerts"]

    assert view["region_known"] is False
    assert view["candidate_known"] is False
    assert view["sensors"][0]["known"] is False
    assert view["warnings"] == [
        "Region R-SYN-A is not in the map.",
        "Candidate pipe p999 is not in the map.",
        "Sensor n77 not in the map.",
    ]


def test_empty_list_draws_the_network_alone() -> None:
    payload = map_payload(parse_map(MINI), None, [])

    assert payload["alerts"] == []
    assert payload["selected"] is None
    assert len(payload["links"]) == 2


def test_replay_is_time_ordered_deduplicated_and_includes_the_selection() -> None:
    late = alert(alert_id="A-late", window_end="2019-03-14T18:00:00Z")
    early = alert(alert_id="A-early", window_end="2019-03-14T09:00:00Z")
    picked = alert(alert_id="A-picked", window_end="2019-03-14T12:00:00Z")
    payload = map_payload(parse_map(MINI), picked, [late, early, late])

    assert [a["id"] for a in payload["alerts"]] == ["A-early", "A-picked", "A-late"]
    assert payload["selected"] == "A-picked"


def test_ids_cannot_escape_the_json_script() -> None:
    hostile = alert(
        alert_id="A-</script><script>alert(1)</script>",
        region="<img src=x onerror=alert(1)>",
        candidate_pipe="p2\u2028&",
    )
    document = map_document(parse_map(MINI), hostile, [hostile])

    data_block = document.split('id="map-data">', 1)[1].split("</script>", 1)[0]
    assert "<" not in data_block and ">" not in data_block and "&" not in data_block
    assert "\u2028" not in data_block
    view = doc_payload(document)["alerts"][0]
    assert view["id"] == "A-</script><script>alert(1)</script>"  # intact once parsed
    assert view["region"] == "<img src=x onerror=alert(1)>"
    assert payload_json({"a": "</script>"}) == '{"a":"\\u003c/script\\u003e"}'


def test_map_script_inserts_data_as_text_only() -> None:
    script = (ASSETS / "network_map.js").read_text(encoding="utf-8")

    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "eval(" not in script
    assert "new Function" not in script
    assert "document.write" not in script
    assert "fetch(" not in script and "XMLHttpRequest" not in script  # works offline


def test_document_is_self_contained_and_offline() -> None:
    document = map_document(parse_map(MINI), alert(), [alert()], variant="page")

    assert "/*__" not in document and "__PAYLOAD__" not in document
    assert 'data-variant="page"' in document
    assert "@font-face" in document and "data:font/woff2;base64," in document
    assert not re.search(r"""(?:src|href)=["']https?://""", document)
    assert "@import" not in document
    with pytest.raises(ValueError, match="variant"):
        map_document(parse_map(MINI), None, variant="poster")


def test_srcdoc_iframe_escapes_the_document() -> None:
    frame = map_iframe('<p class="x">a & b</p>', height=300)

    assert frame.startswith("<iframe ")
    assert 'srcdoc="&lt;p class=&quot;x&quot;&gt;a &amp; b&lt;/p&gt;"' in frame
    assert "height:300px" in frame


def _luminance(colour: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(int(colour[i : i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_region_inks_are_distinct_readable_and_not_alert_colours() -> None:
    background = _luminance(BACKGROUND)
    assert len(set(REGION_PALETTE)) == len(REGION_PALETTE) == 9
    assert CORAL not in REGION_PALETTE and ALERT_GREEN not in REGION_PALETTE
    for colour in REGION_PALETTE:
        contrast = (background + 0.05) / (_luminance(colour) + 0.05)
        assert contrast >= 4.3, colour  # above the 3:1 graphics floor, near text AA


def test_real_map_payload_has_nine_regions() -> None:
    payload = map_payload(load_map())

    assert len(payload["regions"]) == 9
    assert len(payload["links"]) == 909
    assert len(payload["sensors"]) == 33


def test_synthetic_demo_playback_is_labelled_and_uses_official_ids() -> None:
    source = PlaybackAlertSource(DEMO)
    alerts = source.list_alerts(AlertQuery(limit=500)).items

    assert source.recording is not None
    assert "SYNTHETIC" in source.recording.note
    assert "not a detector output" in source.recording.note
    assert source.status().mocks_active is True
    assert len(alerts) == 6
    assert all(a.is_mock for a in alerts)
    assert [a.window_end for a in alerts] == sorted((a.window_end for a in alerts), reverse=True)
    views = map_payload(load_map(), None, alerts)["alerts"]
    assert all(v["region_known"] and not v["warnings"] for v in views)
    assert sum(v["candidate_known"] for v in views) == 5  # one alert names no pipe


@pytest.mark.parametrize("run", [CUSUM, DEMO], ids=lambda p: p.name)
@pytest.mark.parametrize("page", ["../dashboard/app.py", "../dashboard/pages/network_map_page.py"])
def test_pages_run_headless(page: str, run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(run))

    app: Any = testing.AppTest.from_file(page).run()

    assert not app.exception
    (frame,) = app.get("iframe")
    payload = doc_payload(frame.proto.srcdoc)
    assert payload["selected"] == "A-20190314T1705Z-1"
    assert any("RECORDED REPLAY" in w.value for w in app.warning)
    assert any("SIMULATED" in w.value for w in app.warning)
    assert any(
        c.value.startswith("Simulated data (BattLeDIM L-Town). Not validated") for c in app.caption
    )


def test_selected_alert_reaches_the_map_with_its_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(CUSUM))

    app: Any = testing.AppTest.from_file("../dashboard/app.py").run()

    view = doc_payload(app.get("iframe")[0].proto.srcdoc)["alerts"][0]
    assert view["warnings"] == ["Region R-SYN-A is not in the map."]


@pytest.mark.parametrize("page", ["../dashboard/app.py", "../dashboard/pages/network_map_page.py"])
def test_recording_note_is_visible(page: str, monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(DEMO))

    app: Any = testing.AppTest.from_file(page).run()

    assert any("· Recording note: SYNTHETIC" in c.value for c in app.caption)


def test_map_page_keeps_the_svg_download(monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(DEMO))

    app: Any = testing.AppTest.from_file("../dashboard/pages/network_map_page.py").run()

    assert not app.exception
    (download,) = app.get("download_button")
    assert download.proto.label == "Download SVG"


def test_selection_without_alerts_list_still_returns_the_alert() -> None:
    picked: Alert = alert()
    assert map_payload(parse_map(MINI), picked)["alerts"][0]["id"] == picked.alert_id
