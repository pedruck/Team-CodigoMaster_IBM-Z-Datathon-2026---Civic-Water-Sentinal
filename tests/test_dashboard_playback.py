"""Playback adapter: a recorded run served through the same ``AlertSource`` seam.

The API's own ``AlertStore`` is the reference for list semantics (newest first, inclusive
``since``/``until`` on ``window_end``, region, limit/offset).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.config import source_from_env
from dashboard.source import (
    AlertNotFoundError,
    AlertQuery,
    ContractMismatchError,
    HttpAlertSource,
    PlaybackAlertSource,
    Recording,
    SourceError,
)
from sentinel.schemas.alerts import Alert
from sentinel.services.stores import AlertStore

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "alert.mock.json"
T0 = datetime(2019, 3, 14, 12, 0, tzinfo=UTC)


def make_alerts() -> list[Alert]:
    base = Alert.model_validate_json(EXAMPLE.read_text())
    regions = ["R1", None, "R2", "R1", None]
    out = []
    for i, region in enumerate(regions):
        end = T0 + timedelta(minutes=5 * i)
        out.append(
            base.model_copy(
                update={
                    "alert_id": f"A-{end:%Y%m%dT%H%M}Z-1",
                    "window_end": end,
                    "region": region,
                }
            )
        )
    return out


def write_run(directory: Path, alerts: list[Alert], status: str = "{}") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "alerts.jsonl").write_text("".join(a.model_dump_json() + "\n" for a in alerts))
    (directory / "status.json").write_text(status)
    (directory / "run.json").write_text(
        json.dumps({"run_id": "test-run", "recorded_at": "2026-10-17", "note": "unit test"})
    )
    return directory


QUERIES = [
    AlertQuery(),
    AlertQuery(limit=2),
    AlertQuery(limit=2, offset=2),
    AlertQuery(offset=10),
    AlertQuery(since=T0 + timedelta(minutes=5)),
    AlertQuery(until=T0 + timedelta(minutes=10)),
    AlertQuery(since=T0 + timedelta(minutes=5), until=T0 + timedelta(minutes=5)),
    AlertQuery(region="R1"),
    AlertQuery(region="R9"),
]


@pytest.mark.parametrize("query", QUERIES)
def test_list_matches_api_store(tmp_path: Path, query: AlertQuery) -> None:
    alerts = make_alerts()
    store = AlertStore(capacity=100)
    for a in alerts:
        store.add(a)
    expected, total = store.query(
        limit=query.limit,
        offset=query.offset,
        since=query.since,
        until=query.until,
        region=query.region,
    )

    page = PlaybackAlertSource(write_run(tmp_path / "run", alerts)).list_alerts(query)

    assert [a.alert_id for a in page.items] == [a.alert_id for a in expected]
    assert (page.total, page.limit, page.offset) == (total, query.limit, query.offset)


@pytest.fixture
def status_body(client: TestClient) -> str:
    return client.get("/v1/status").text


def test_get_alert_by_id_and_not_found(tmp_path: Path) -> None:
    alerts = make_alerts()
    source = PlaybackAlertSource(write_run(tmp_path / "run", alerts))

    assert source.get_alert(alerts[2].alert_id) == alerts[2]
    with pytest.raises(AlertNotFoundError):
        source.get_alert("A-19990101T0000Z-1")


def test_status_health_and_recording(tmp_path: Path, status_body: str) -> None:
    source = PlaybackAlertSource(write_run(tmp_path / "run", make_alerts(), status_body))

    assert source.status().mocks_active is True
    assert source.health().status == "ok"
    assert source.recording == Recording(
        run_id="test-run", recorded_at="2026-10-17", note="unit test"
    )


def test_malformed_record_is_a_contract_mismatch(tmp_path: Path) -> None:
    run = write_run(tmp_path / "run", make_alerts())
    with (run / "alerts.jsonl").open("a") as f:
        f.write('{"alert_id": "A-broken"}\n')

    with pytest.raises(ContractMismatchError, match=r"alerts\.jsonl:6"):
        PlaybackAlertSource(run).list_alerts(AlertQuery())


def test_http_source_is_live() -> None:
    assert HttpAlertSource("http://127.0.0.1:9").recording is None


def test_config_selects_playback_when_a_run_dir_is_set(tmp_path: Path) -> None:
    run = write_run(tmp_path / "run", make_alerts())

    assert isinstance(
        source_from_env({"SENTINEL_DASHBOARD_PLAYBACK_DIR": str(run)}), PlaybackAlertSource
    )
    assert isinstance(source_from_env({}), HttpAlertSource)


def test_app_shows_recorded_replay_banner(
    tmp_path: Path, status_body: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    run = write_run(tmp_path / "run", make_alerts(), status_body)
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(run))

    app = testing.AppTest.from_file("../dashboard/app.py").run()

    assert not app.exception
    banners = [e.value for e in app.error] + [w.value for w in app.warning]
    assert any("RECORDED REPLAY — run test-run, 2026-10-17" in b for b in banners)
    assert {m.label: m.value for m in app.metric}["API"] == "recorded replay"


SHIPPED = Path(__file__).resolve().parents[1] / "dashboard" / "playback"
NULLABLE = (
    "region",
    "suspected_onset",
    "hours_since_onset",
    "cusum",
    "forecast_contributions_m",
    "summary",
    "audit",
)


MALFORMED = SHIPPED / "fixture-malformed"


def shipped_runs() -> list[Path]:
    return sorted(p for p in SHIPPED.iterdir() if p.is_dir() and p != MALFORMED)


def test_shipped_runs_exist() -> None:
    assert {p.name for p in shipped_runs()} >= {"fixture-edge-cases", "fixture-empty"}


def test_shipped_malformed_run_is_a_contract_mismatch() -> None:
    with pytest.raises(ContractMismatchError):
        PlaybackAlertSource(MALFORMED).list_alerts(AlertQuery())


@pytest.mark.parametrize("run", shipped_runs(), ids=lambda p: p.name)
def test_shipped_run_is_contract_valid(run: Path) -> None:
    source = PlaybackAlertSource(run)

    page = source.list_alerts(AlertQuery(limit=500))
    assert source.status().mocks_active is True  # fixtures come from mock runs only
    assert source.recording is not None
    assert all(a.is_mock for a in page.items)


def test_edge_case_run_covers_the_contract_edges() -> None:
    items = PlaybackAlertSource(SHIPPED / "fixture-edge-cases").list_alerts(AlertQuery()).items

    assert any(all(getattr(a, f) is None for f in NULLABLE) for a in items)
    assert any(not a.deviating_sensors for a in items)
    assert any(all(getattr(a, f) is not None for f in NULLABLE) for a in items)


def test_empty_run_has_no_alerts() -> None:
    page = PlaybackAlertSource(SHIPPED / "fixture-empty").list_alerts(AlertQuery())

    assert page.total == 0


def test_app_reports_an_unusable_playback_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(tmp_path / "missing"))

    app = testing.AppTest.from_file("../dashboard/app.py").run()

    assert not app.exception
    assert any("run.json" in e.value for e in app.error)


@pytest.mark.parametrize("missing", ["alerts.jsonl", "status.json"])
def test_unreadable_run_file_is_a_source_error(tmp_path: Path, missing: str) -> None:
    run = write_run(tmp_path / "run", make_alerts())
    (run / missing).unlink()
    source = PlaybackAlertSource(run)

    with pytest.raises(SourceError, match=missing):
        source.status() if missing == "status.json" else source.list_alerts(AlertQuery())


def test_run_without_run_json_is_named_after_its_directory(tmp_path: Path) -> None:
    run = write_run(tmp_path / "eval-2019-x", make_alerts())
    (run / "run.json").unlink()

    assert PlaybackAlertSource(run).recording == Recording(
        run_id="eval-2019-x", recorded_at="unknown date", note=""
    )


@pytest.mark.parametrize("limit", [0, 501])
def test_query_limit_has_the_api_bounds(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        AlertQuery(limit=limit)
