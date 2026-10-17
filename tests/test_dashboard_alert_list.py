"""'Recent alerts' list, through the headless app on recorded runs.

Expected values are literals read off the shipped fixtures (status last window 2019-03-14
23:55 UTC; mock alert at 16:40 with deviating sensors n31, n4, n1), never recomputed.
"""

import json
from pathlib import Path
from typing import Any

import pytest

testing = pytest.importorskip("streamlit.testing.v1")

PLAYBACK = Path(__file__).resolve().parents[1] / "dashboard" / "playback"


def run_app(monkeypatch: pytest.MonkeyPatch, run: Path) -> Any:
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(run))
    app = testing.AppTest.from_file("../dashboard/app.py").run()
    assert not app.exception
    return app


def rows(app: Any) -> list[dict[str, Any]]:
    table = app.dataframe[0].value
    return list(table.to_dict("records"))


def test_recent_alerts_are_listed_newest_stored_first(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, PLAYBACK / "fixture-edge-cases")

    assert [r["Alert"] for r in rows(app)] == [
        "A-20190314T1640Z-93",
        "A-20190314T1640Z-92",
        "A-20190314T1640Z-91",
        "A-20190314T1640Z-1",
    ]
    assert any("Recent alerts" in h.value for h in app.subheader)


def test_mock_alert_row_shows_evidence_age_and_badge(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, PLAYBACK / "fixture-edge-cases")
    row = next(r for r in rows(app) if r["Alert"] == "A-20190314T1640Z-1")

    assert row["Window end"] == "2019-03-14 16:40 UTC"
    assert row["Region"] == "region not available"
    assert row["Sensors"] == 3
    assert row["Strongest deviation"] == "n31 -5.06 m"
    assert row["z"] == "5.06"
    assert row["Estimated onset"] == "2019-03-14 16:40 UTC"
    assert row["Age (simulated)"] == "7 h 15 min"
    assert row["Mock"] == "SIMULATED"


def test_all_null_and_empty_sensor_rows_render(monkeypatch: pytest.MonkeyPatch) -> None:
    by_id = {r["Alert"]: r for r in rows(run_app(monkeypatch, PLAYBACK / "fixture-edge-cases"))}

    all_null = by_id["A-20190314T1640Z-91"]
    assert (all_null["Region"], all_null["Estimated onset"]) == (
        "region not available",
        "not provided",
    )
    no_sensors = by_id["A-20190314T1640Z-92"]
    assert (no_sensors["Sensors"], no_sensors["Strongest deviation"], no_sensors["z"]) == (
        0,
        "no deviating sensor",
        "no deviating sensor",
    )
    assert by_id["A-20190314T1640Z-93"]["Region"] == "R-FIXTURE"


def test_empty_run_shows_the_empty_state(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, PLAYBACK / "fixture-empty")

    assert any("No alerts in this period" in i.value for i in app.info)
    assert len(app.dataframe) == 0


def test_malformed_run_shows_contract_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, PLAYBACK / "fixture-malformed")

    assert any("alerts do not match the contract" in e.value for e in app.error)
    assert len(app.dataframe) == 0


def test_non_mock_alert_has_no_simulated_badge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = first_fixture_alert()
    first["is_mock"] = False  # test-only record, never shipped

    (row,) = rows(run_app(monkeypatch, write_run(tmp_path, [first])))

    assert row["Mock"] == ""


def write_run(tmp_path: Path, alerts: list[dict[str, Any]]) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "alerts.jsonl").write_text("".join(json.dumps(a) + "\n" for a in alerts))
    (run / "status.json").write_text((PLAYBACK / "fixture-edge-cases" / "status.json").read_text())
    return run


def first_fixture_alert() -> dict[str, Any]:
    lines = (PLAYBACK / "fixture-edge-cases" / "alerts.jsonl").read_text().splitlines()
    alert: dict[str, Any] = json.loads(lines[0])
    return alert


def test_newest_window_end_first_and_null_z_is_not_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    early, late = first_fixture_alert(), first_fixture_alert()
    early.update(alert_id="A-20190314T1000Z-1", window_end="2019-03-14T10:00:00Z")
    late["deviating_sensors"][0]["z"] = None

    table = rows(run_app(monkeypatch, write_run(tmp_path, [early, late])))

    assert [r["Alert"] for r in table] == ["A-20190314T1640Z-1", "A-20190314T1000Z-1"]
    assert [r["Age (simulated)"] for r in table] == ["7 h 15 min", "13 h 55 min"]
    assert table[0]["z"] == "not provided"


def test_alert_after_the_last_window_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    future = first_fixture_alert()
    future.update(alert_id="A-20190315T0100Z-1", window_end="2019-03-15T01:00:00Z")

    (row,) = rows(run_app(monkeypatch, write_run(tmp_path, [future])))

    assert row["Age (simulated)"] == "after the last window"
