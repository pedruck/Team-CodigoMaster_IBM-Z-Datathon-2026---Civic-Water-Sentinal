"""Polling of the 'Recent alerts' list: merge/dedup, back-off and failure states (UI-005).

Expected values are literals (worked examples, fixture IDs), never recomputed the way the
code does. Wall-clock and monotonic times are injected, so no test sleeps.
"""

import json
import re
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dashboard.config import refresh_interval_s
from dashboard.refresh import PollState, backoff_s, merge_newest_first, needs_full_rerun, poll
from dashboard.source import HttpAlertSource, PlaybackAlertSource
from sentinel.schemas.alerts import Alert
from tests.test_dashboard_source import Stub

PLAYBACK = Path(__file__).resolve().parents[1] / "dashboard" / "playback"
WALL = datetime(2026, 10, 17, 14, 3, 27, tzinfo=UTC)
DOWN = "http://127.0.0.1:9"


def fixture_alert(alert_id: str, window_end: str) -> dict[str, Any]:
    line = (PLAYBACK / "fixture-edge-cases" / "alerts.jsonl").read_text().splitlines()[0]
    alert: dict[str, Any] = json.loads(line)
    alert.update(alert_id=alert_id, window_end=window_end)
    return alert


def model(alert_id: str, window_end: str) -> Alert:
    return Alert.model_validate(fixture_alert(alert_id, window_end))


def write_run(run: Path, alerts: list[dict[str, Any]]) -> Path:
    run.mkdir(parents=True, exist_ok=True)
    (run / "alerts.jsonl").write_text("".join(json.dumps(a) + "\n" for a in alerts))
    (run / "status.json").write_text((PLAYBACK / "fixture-edge-cases" / "status.json").read_text())
    return run


def append(run: Path, *lines: str) -> None:
    with (run / "alerts.jsonl").open("a") as f:
        f.writelines(line + "\n" for line in lines)


def ids(state: PollState) -> list[str]:
    return [a.alert_id for a in state.alerts]


# --- pure module ---------------------------------------------------------------------------


def test_merge_puts_new_alerts_first_and_never_repeats_an_id() -> None:
    shown = [model("A-1645-1", "2019-03-14T16:45:00Z"), model("A-1640-1", "2019-03-14T16:40:00Z")]
    fresh = [
        model("A-1650-1", "2019-03-14T16:50:00Z"),
        model("A-1645-2", "2019-03-14T16:45:00Z"),
        model("A-1645-1", "2019-03-14T16:45:00Z"),  # since is inclusive: seen again
    ]

    merged = merge_newest_first(shown, fresh, keep=50)

    assert [a.alert_id for a in merged] == ["A-1650-1", "A-1645-2", "A-1645-1", "A-1640-1"]


def test_merge_keeps_only_the_most_recent() -> None:
    shown = [model("A-1645-1", "2019-03-14T16:45:00Z"), model("A-1640-1", "2019-03-14T16:40:00Z")]
    fresh = [model("A-1650-1", "2019-03-14T16:50:00Z")]

    assert [a.alert_id for a in merge_newest_first(shown, fresh, keep=2)] == [
        "A-1650-1",
        "A-1645-1",
    ]


@pytest.mark.parametrize(
    ("failures", "expected"),
    [(0, 5.0), (1, 10.0), (2, 20.0), (3, 40.0), (4, 60.0), (5, 60.0), (30, 60.0)],
)
def test_backoff_doubles_from_the_refresh_interval_and_caps_at_60_s(
    failures: int, expected: float
) -> None:
    assert backoff_s(failures, base_s=5.0) == expected


def test_backoff_with_a_short_interval_still_caps_at_60_s() -> None:
    assert [backoff_s(n, base_s=2.0) for n in range(7)] == [2, 4, 8, 16, 32, 60, 60]


def test_refresh_interval_is_configurable() -> None:
    assert refresh_interval_s({}) == 5.0
    assert refresh_interval_s({"SENTINEL_DASHBOARD_REFRESH_S": "2.5"}) == 2.5
    with pytest.raises(ValueError, match="SENTINEL_DASHBOARD_REFRESH_S"):
        refresh_interval_s({"SENTINEL_DASHBOARD_REFRESH_S": "0"})


# --- polling against a recorded run ---------------------------------------------------------


def test_two_overlapping_refreshes_yield_no_duplicates(tmp_path: Path) -> None:
    run = write_run(
        tmp_path / "run",
        [
            fixture_alert("A-20190314T1635Z-1", "2019-03-14T16:35:00Z"),
            fixture_alert("A-20190314T1640Z-1", "2019-03-14T16:40:00Z"),
        ],
    )
    source = PlaybackAlertSource(run)
    first = poll(PollState(), source, base_s=5.0, wall_now=WALL, mono_now=0.0)
    append(
        run,
        json.dumps(fixture_alert("A-20190314T1640Z-2", "2019-03-14T16:40:00Z")),
        json.dumps(fixture_alert("A-20190314T1645Z-1", "2019-03-14T16:45:00Z")),
    )

    second = poll(first, source, base_s=5.0, wall_now=WALL, mono_now=5.0)

    assert ids(first) == ["A-20190314T1640Z-1", "A-20190314T1635Z-1"]
    assert ids(second) == [
        "A-20190314T1645Z-1",
        "A-20190314T1640Z-2",
        "A-20190314T1640Z-1",
        "A-20190314T1635Z-1",
    ]
    assert second.error is None


@pytest.fixture
def stub() -> Iterator[Stub]:
    s = Stub()
    thread = threading.Thread(target=s.server.serve_forever, daemon=True)
    thread.start()
    yield s
    s.server.shutdown()
    s.server.server_close()


def test_refresh_asks_since_the_newest_window_end_shown(stub: Stub) -> None:
    page = {
        "items": [fixture_alert("A-20190314T1640Z-1", "2019-03-14T16:40:00Z")],
        "total": 1,
        "limit": 50,
        "offset": 0,
    }
    stub.routes = {
        "/health": (200, b'{"status": "ok", "version": "0.1.0"}'),
        "/v1/status": (200, (PLAYBACK / "fixture-edge-cases" / "status.json").read_bytes()),
        "/v1/alerts": (200, json.dumps(page).encode()),
    }
    source = HttpAlertSource(stub.url)

    state = poll(PollState(), source, base_s=5.0, wall_now=WALL, mono_now=0.0)
    poll(state, source, base_s=5.0, wall_now=WALL, mono_now=5.0)

    alert_requests = [r for r in stub.requests if r.startswith("/v1/alerts")]
    assert alert_requests == [
        "/v1/alerts?limit=50&offset=0",
        "/v1/alerts?limit=500&offset=0&since=2019-03-14T16%3A40%3A00%2B00%3A00",
    ]


def test_malformed_record_after_a_refresh_is_a_contract_mismatch(tmp_path: Path) -> None:
    run = write_run(tmp_path / "run", [fixture_alert("A-20190314T1640Z-1", "2019-03-14T16:40:00Z")])
    source = PlaybackAlertSource(run)
    first = poll(PollState(), source, base_s=5.0, wall_now=WALL, mono_now=0.0)
    append(run, '{"alert_id": "A-broken"}')

    second = poll(first, source, base_s=5.0, wall_now=WALL, mono_now=5.0)

    assert second.error_kind == "mismatch"
    assert second.error is not None and "alerts.jsonl:2" in second.error
    assert second.unavailable_since is None
    assert second.alerts == ()  # never keep data next to a contract mismatch (story 22)


# --- unreachable backend --------------------------------------------------------------------


def test_unreachable_backend_backs_off_and_remembers_when_it_went_down(tmp_path: Path) -> None:
    shown = poll(
        PollState(),
        PlaybackAlertSource(
            write_run(
                tmp_path / "run", [fixture_alert("A-20190314T1640Z-1", "2019-03-14T16:40:00Z")]
            )
        ),
        base_s=5.0,
        wall_now=WALL,
        mono_now=0.0,
    )
    down = HttpAlertSource(DOWN, timeout_s=1)

    first = poll(shown, down, base_s=5.0, wall_now=WALL, mono_now=100.0)
    skipped = poll(first, down, base_s=5.0, wall_now=WALL, mono_now=105.0)
    later = datetime(2026, 10, 17, 14, 4, 0, tzinfo=UTC)
    second = poll(skipped, down, base_s=5.0, wall_now=later, mono_now=110.0)

    assert (first.error_kind, first.failures, first.retry_at) == ("unavailable", 1, 110.0)
    assert first.unavailable_since == WALL
    assert ids(first) == ["A-20190314T1640Z-1"]  # stale data is kept, under the banner
    assert skipped == first  # not due yet: no request
    assert (second.failures, second.retry_at) == (2, 130.0)
    assert second.unavailable_since == WALL  # first failure, not the latest


def test_recovery_clears_the_failure_and_flags_it(tmp_path: Path) -> None:
    run = write_run(tmp_path / "run", [fixture_alert("A-20190314T1640Z-1", "2019-03-14T16:40:00Z")])
    failed = poll(PollState(), HttpAlertSource(DOWN, timeout_s=1), 5.0, WALL, 0.0)

    recovered = poll(failed, PlaybackAlertSource(run), 5.0, WALL, 10.0)

    assert (recovered.failures, recovered.error, recovered.unavailable_since) == (0, None, None)
    assert recovered.recovered is True
    assert ids(recovered) == ["A-20190314T1640Z-1"]


# --- headless app ---------------------------------------------------------------------------


def run_app(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Any:
    testing = pytest.importorskip("streamlit.testing.v1")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = testing.AppTest.from_file("../dashboard/app.py").run()
    assert not app.exception
    return app


def recent_ids(app: Any) -> list[str]:
    return [r["Alert"] for r in app.dataframe[0].value.to_dict("records")]


def test_app_refresh_with_overlapping_data_shows_no_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = write_run(tmp_path / "run", [fixture_alert("A-20190314T1640Z-1", "2019-03-14T16:40:00Z")])
    app = run_app(monkeypatch, {"SENTINEL_DASHBOARD_PLAYBACK_DIR": str(run)})
    append(
        run,
        json.dumps(fixture_alert("A-20190314T1640Z-2", "2019-03-14T16:40:00Z")),
        json.dumps(fixture_alert("A-20190314T1645Z-1", "2019-03-14T16:45:00Z")),
    )

    app.run()

    assert not app.exception
    assert recent_ids(app) == ["A-20190314T1645Z-1", "A-20190314T1640Z-2", "A-20190314T1640Z-1"]


def test_app_shows_unavailable_since_banner_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(
        monkeypatch,
        {"SENTINEL_DASHBOARD_API_URL": DOWN, "SENTINEL_DASHBOARD_TIMEOUT_S": "1"},
    )

    banners = [e.value for e in app.error]
    assert any(
        re.match(r"API unavailable since \d\d:\d\d UTC .*retrying in 10 s", b) for b in banners
    ), banners


def test_app_hides_data_on_contract_mismatch_after_a_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = write_run(tmp_path / "run", [fixture_alert("A-20190314T1640Z-1", "2019-03-14T16:40:00Z")])
    app = run_app(monkeypatch, {"SENTINEL_DASHBOARD_PLAYBACK_DIR": str(run)})
    assert recent_ids(app) == ["A-20190314T1640Z-1"]
    append(run, '{"alert_id": "A-broken"}')

    app.run()

    assert not app.exception
    assert any("alerts do not match the contract" in e.value for e in app.error)
    assert len(app.dataframe) == 0


# --- review fixes: full rerun, truncation ----------------------------------------------------


def test_full_rerun_when_simulated_time_or_alerts_change(tmp_path: Path) -> None:
    run = write_run(tmp_path / "run", [fixture_alert("A-20190314T1640Z-1", "2019-03-14T16:40:00Z")])
    source = PlaybackAlertSource(run)
    first = poll(PollState(), source, base_s=5.0, wall_now=WALL, mono_now=0.0)
    same = poll(first, source, base_s=5.0, wall_now=WALL, mono_now=5.0)
    append(run, json.dumps(fixture_alert("A-20190314T1645Z-1", "2019-03-14T16:45:00Z")))
    grown = poll(same, source, base_s=5.0, wall_now=WALL, mono_now=10.0)

    assert needs_full_rerun(PollState(), first) is False  # first load: the page is fresh
    assert needs_full_rerun(first, same) is False
    assert needs_full_rerun(same, grown) is True


def test_full_rerun_when_the_simulated_clock_moves(tmp_path: Path) -> None:
    run = write_run(tmp_path / "run", [fixture_alert("A-20190314T1640Z-1", "2019-03-14T16:40:00Z")])
    source = PlaybackAlertSource(run)
    first = poll(PollState(), source, base_s=5.0, wall_now=WALL, mono_now=0.0)
    status = json.loads((run / "status.json").read_text())
    status["last_timestamp"] = "2019-03-15T00:00:00Z"
    (run / "status.json").write_text(json.dumps(status))

    moved = poll(first, source, base_s=5.0, wall_now=WALL, mono_now=5.0)

    assert needs_full_rerun(first, moved) is True


def test_more_new_alerts_than_one_request_is_flagged(tmp_path: Path) -> None:
    first_alert = fixture_alert("A-20190314T0000Z-1", "2019-03-14T00:00:00Z")
    run = write_run(tmp_path / "run", [first_alert])
    source = PlaybackAlertSource(run)
    first = poll(PollState(), source, base_s=5.0, wall_now=WALL, mono_now=0.0)
    burst = [
        json.dumps(fixture_alert(f"A-20190314T0001Z-{n}", "2019-03-14T00:05:00Z"))
        for n in range(501)
    ]
    append(run, *burst)

    second = poll(first, source, base_s=5.0, wall_now=WALL, mono_now=5.0)

    assert second.truncated is True
    assert first.truncated is False
