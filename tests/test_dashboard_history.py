"""Paginated alert history with time-range and region filters (UI-005).

API semantics (contract §5): newest first, ``since``/``until`` inclusive on ``window_end``,
exact ``region``, ``limit``/``offset``. Expected IDs are literals for a hand-made run of five
alerts at 12:00..12:20 UTC with regions R1, -, R2, R1, - (oldest first).
"""

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

# dashboard.history imports streamlit at module level; streamlit has no s390x build (no pyarrow
# wheel), so on the LinuxONE VM this module is skipped instead of failing collection.
pytest.importorskip("streamlit")

from dashboard.history import history_query, page_count, parse_utc
from dashboard.source import AlertQuery
from tests.test_dashboard_source import Stub

PLAYBACK = Path(__file__).resolve().parents[1] / "dashboard" / "playback"
STATUS = PLAYBACK / "fixture-edge-cases" / "status.json"


def fixture_alert(alert_id: str, window_end: str, region: str | None) -> dict[str, Any]:
    line = (PLAYBACK / "fixture-edge-cases" / "alerts.jsonl").read_text().splitlines()[0]
    alert: dict[str, Any] = json.loads(line)
    alert.update(alert_id=alert_id, window_end=window_end, region=region)
    return alert


RUN = [
    fixture_alert("A-20190314T1200Z-1", "2019-03-14T12:00:00Z", "R1"),
    fixture_alert("A-20190314T1205Z-1", "2019-03-14T12:05:00Z", None),
    fixture_alert("A-20190314T1210Z-1", "2019-03-14T12:10:00Z", "R2"),
    fixture_alert("A-20190314T1215Z-1", "2019-03-14T12:15:00Z", "R1"),
    fixture_alert("A-20190314T1220Z-1", "2019-03-14T12:20:00Z", None),
]


# --- pure helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("   ", None),
        ("2019-03-14 12:05", datetime(2019, 3, 14, 12, 5, tzinfo=UTC)),
        ("2019-03-14T12:05:00Z", datetime(2019, 3, 14, 12, 5, tzinfo=UTC)),
        ("2019-03-14", datetime(2019, 3, 14, 0, 0, tzinfo=UTC)),
        ("2019-03-14T13:05+01:00", datetime(2019, 3, 14, 12, 5, tzinfo=UTC)),
    ],
)
def test_parse_utc_reads_naive_input_as_utc(text: str, expected: datetime | None) -> None:
    parsed = parse_utc(text)

    assert parsed == expected
    if parsed is not None:
        assert parsed.utcoffset() == timedelta(0)


def test_parse_utc_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="yesterday"):
        parse_utc("yesterday")


def test_history_query_maps_page_number_to_offset() -> None:
    since = datetime(2019, 3, 14, 12, 5, tzinfo=UTC)

    query = history_query(page=3, page_size=10, since=since, until=None, region=" R1 ")

    assert query == AlertQuery(limit=10, offset=20, since=since, until=None, region="R1")


def test_history_query_blank_region_is_no_filter() -> None:
    assert history_query(page=1, page_size=25, since=None, until=None, region="  ").region is None


@pytest.mark.parametrize(
    ("total", "size", "pages"), [(0, 25, 1), (5, 2, 3), (4, 2, 2), (26, 25, 2)]
)
def test_page_count(total: int, size: int, pages: int) -> None:
    assert page_count(total, size) == pages


def test_parse_utc_keeps_the_instant_of_aware_input() -> None:
    eastern = timezone(timedelta(hours=-3))

    assert parse_utc("2019-03-14T09:05-03:00") == datetime(2019, 3, 14, 9, 5, tzinfo=eastern)


# --- headless app on a recorded run ---------------------------------------------------------


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    testing = pytest.importorskip("streamlit.testing.v1")
    run = tmp_path / "run"
    run.mkdir()
    (run / "alerts.jsonl").write_text("".join(json.dumps(a) + "\n" for a in RUN))
    (run / "status.json").write_text(STATUS.read_text())
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(run))
    at = testing.AppTest.from_file("../dashboard/app.py").run()
    assert not at.exception
    return at


def history_ids(at: Any) -> list[str]:
    assert not at.exception
    return [r["Alert"] for r in at.dataframe[1].value.to_dict("records")]


def captions(at: Any) -> list[str]:
    return [c.value for c in at.caption]


def test_history_lists_everything_newest_first(app: Any) -> None:
    assert any("History" in h.value for h in app.subheader)
    assert history_ids(app) == [
        "A-20190314T1220Z-1",
        "A-20190314T1215Z-1",
        "A-20190314T1210Z-1",
        "A-20190314T1205Z-1",
        "A-20190314T1200Z-1",
    ]


def test_history_second_page(app: Any) -> None:
    app.number_input(key="history_page_size").set_value(2).run()
    app.number_input(key="history_page").set_value(2).run()

    assert history_ids(app) == ["A-20190314T1210Z-1", "A-20190314T1205Z-1"]
    assert "Page 2 of 3 — alerts 3 to 4 of 5." in captions(app)


def test_history_time_range_is_inclusive(app: Any) -> None:
    app.text_input(key="history_since").set_value("2019-03-14 12:05")
    app.text_input(key="history_until").set_value("2019-03-14 12:15").run()

    assert history_ids(app) == [
        "A-20190314T1215Z-1",
        "A-20190314T1210Z-1",
        "A-20190314T1205Z-1",
    ]


def test_history_region_filter(app: Any) -> None:
    app.text_input(key="history_region").set_value("R1").run()

    assert history_ids(app) == ["A-20190314T1215Z-1", "A-20190314T1200Z-1"]


def test_history_page_past_the_end(app: Any) -> None:
    app.number_input(key="history_page_size").set_value(2).run()
    app.number_input(key="history_page").set_value(4).run()

    assert not app.exception
    assert len(app.dataframe) == 1  # only the recent list
    assert any("No alerts on page 4" in i.value and "last page is 3" in i.value for i in app.info)


def test_history_empty_filter_result_is_the_empty_state(app: Any) -> None:
    app.text_input(key="history_region").set_value("R9").run()

    assert len(app.dataframe) == 1
    assert any("No alerts match the history filters" in i.value for i in app.info)


def test_history_rejects_an_unreadable_time(app: Any) -> None:
    app.text_input(key="history_since").set_value("yesterday").run()

    assert not app.exception
    assert any("yesterday" in e.value for e in app.error)
    assert len(app.dataframe) == 1


# --- through the HTTP adapter ---------------------------------------------------------------


@pytest.fixture
def stub() -> Iterator[Stub]:
    s = Stub()
    thread = threading.Thread(target=s.server.serve_forever, daemon=True)
    thread.start()
    yield s
    s.server.shutdown()
    s.server.server_close()


def test_history_sends_api_parameters(stub: Stub, monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    empty = b'{"items": [], "total": 0, "limit": 50, "offset": 0}'
    stub.routes = {
        "/health": (200, b'{"status": "ok", "version": "0.1.0"}'),
        "/v1/status": (200, STATUS.read_bytes()),
        "/v1/alerts": (200, empty),
    }
    monkeypatch.setenv("SENTINEL_DASHBOARD_API_URL", stub.url)
    at = testing.AppTest.from_file("../dashboard/app.py").run()

    at.number_input(key="history_page_size").set_value(10)
    at.number_input(key="history_page").set_value(3)
    at.text_input(key="history_since").set_value("2019-03-14 12:05")
    at.text_input(key="history_until").set_value("2019-03-14T12:15:00Z")
    at.text_input(key="history_region").set_value("R1").run()

    assert not at.exception
    assert stub.requests[-1] == (
        "/v1/alerts?limit=10&offset=20"
        "&since=2019-03-14T12%3A05%3A00%2B00%3A00"
        "&until=2019-03-14T12%3A15%3A00%2B00%3A00&region=R1"
    )
