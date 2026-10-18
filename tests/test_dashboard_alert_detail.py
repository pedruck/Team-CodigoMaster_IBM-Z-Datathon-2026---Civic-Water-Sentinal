"""Alert detail (UI-004), through the headless app on recorded runs.

Expected values are literals read off ``dashboard/playback/fixture-edge-cases/alerts.jsonl``
(-1 real mock alert, -91 every nullable field null and audit null, -92 no deviating sensors,
-93 every optional field present), never recomputed.
"""

from pathlib import Path
from typing import Any

import pytest

testing = pytest.importorskip("streamlit.testing.v1")

PLAYBACK = Path(__file__).resolve().parents[1] / "dashboard" / "playback"
EDGE = PLAYBACK / "fixture-edge-cases"
SELECT_KEY = "alert_detail_id"


def run_app(monkeypatch: pytest.MonkeyPatch, run: Path, alert_id: str | None = None) -> Any:
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(run))
    app = testing.AppTest.from_file("../dashboard/app.py").run()
    assert not app.exception
    if alert_id is not None:
        app = app.selectbox(key=SELECT_KEY).select(alert_id).run()
        assert not app.exception
    return app


def table_with(app: Any, column: str) -> list[dict[str, Any]] | None:
    for table in app.table:
        frame = table.value
        if column in frame.columns:
            return list(frame.to_dict("records"))
    return None


def facts(app: Any) -> dict[str, str]:
    rows = table_with(app, "Evidence")
    assert rows is not None, "evidence table not rendered"
    return {r["Evidence"]: r["Value"] for r in rows}


def texts(app: Any) -> list[str]:
    elements = [*app.markdown, *app.caption, *app.info, *app.warning, *app.error, *app.header]
    return [str(e.value) for e in elements]


def test_detail_defaults_to_the_newest_alert_on_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, EDGE)

    assert app.selectbox(key=SELECT_KEY).value == "A-20190314T1640Z-93"
    assert app.selectbox(key=SELECT_KEY).options == [
        "A-20190314T1640Z-93",
        "A-20190314T1640Z-92",
        "A-20190314T1640Z-91",
        "A-20190314T1640Z-1",
    ]
    assert any("Alert detail" in h.value for h in app.header)


def test_full_alert_shows_every_evidence_field_as_received(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = run_app(monkeypatch, EDGE, "A-20190314T1640Z-93")

    assert facts(app) == {
        "Alert": "A-20190314T1640Z-93",
        "Window end": "2019-03-14 16:40 UTC",
        "Region": "R-FIXTURE",
        "CUSUM statistic vs threshold": "7.5 vs threshold 5.0",
        "Estimated onset": "2019-03-14 16:40 UTC",
        "Hours since onset (backend)": "0.0",
        "Detector summary": "[FIXTURE] every optional field present (invented values)",
        "Forecaster": "mock-persistence 0.1.0 (mock)",
        "Detector": "mock-threshold 0.1.0 (mock)",
        "Audit reference": (
            "seq 1, hash 0000000000000000000000000000000000000000000000000000000000000000"
        ),
        "Recorded by server": "2026-10-17 21:22:30 UTC",
    }
    assert table_with(app, "Deviation (m)") == [
        {"Sensor": "n31", "Deviation (m)": "-5.059899999999999", "z": "5.059899999999999",
         "Since": "not provided"},
        {"Sensor": "n4", "Deviation (m)": "-4.955100000000002", "z": "4.955100000000002",
         "Since": "not provided"},
        {"Sensor": "n1", "Deviation (m)": "-4.9542", "z": "4.9542", "Since": "not provided"},
    ]  # fmt: skip
    assert table_with(app, "Contribution (m)") == [
        {"Sensor": "n31", "Feature": "hour_of_day", "Contribution (m)": "0.4"},
        {"Sensor": "n31", "Feature": "flow_p227", "Contribution (m)": "-0.2"},
    ]
    assert any("explanation unavailable" in i.value for i in app.info)
    assert any("SIMULATED" in w.value for w in app.warning)


def test_all_null_alert_marks_every_absent_field(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, EDGE, "A-20190314T1640Z-91")

    shown = facts(app)
    assert shown["Region"] == "region not available"
    assert shown["CUSUM statistic vs threshold"] == "not provided"
    assert shown["Estimated onset"] == "not provided"
    assert shown["Hours since onset (backend)"] == "not provided"
    assert shown["Detector summary"] == "not provided"
    assert shown["Audit reference"] == "audit unavailable"
    assert table_with(app, "Contribution (m)") is None
    assert any("Forecast contributions: not provided" in c.value for c in app.caption)
    assert any("explanation unavailable" in i.value for i in app.info)


def test_alert_without_deviating_sensors_still_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, EDGE, "A-20190314T1640Z-92")

    assert facts(app)["Alert"] == "A-20190314T1640Z-92"
    assert table_with(app, "Deviation (m)") is None
    assert any("No deviating sensor" in c.value for c in app.caption)


def test_audit_reference_without_hash_is_shown_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, EDGE, "A-20190314T1640Z-1")

    shown = facts(app)
    assert shown["Audit reference"] == "seq 200, hash not provided"
    assert shown["Detector summary"] == "[MOCK] 3 sensors below expected pressure"
    assert shown["Hours since onset (backend)"] == "0.0"


@pytest.mark.parametrize(
    "alert_id",
    ["A-20190314T1640Z-1", "A-20190314T1640Z-91", "A-20190314T1640Z-92", "A-20190314T1640Z-93"],
)
def test_no_verified_wording_anywhere(monkeypatch: pytest.MonkeyPatch, alert_id: str) -> None:
    app = run_app(monkeypatch, EDGE, alert_id)

    rendered = [*texts(app), *(str(t.value.to_dict()) for t in app.table)]
    rendered += [str(d.value.to_dict()) for d in app.dataframe]
    assert rendered
    assert [t for t in rendered if "verif" in t.lower()] == []


def test_created_at_appears_only_in_the_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, EDGE)

    list_text = str(app.dataframe[0].value.to_dict())
    assert "2026-10-17" not in list_text
    assert facts(app)["Recorded by server"] == "2026-10-17 21:22:30 UTC"


@pytest.mark.parametrize("run", ["fixture-empty", "fixture-malformed"])
def test_no_detail_without_a_valid_alert_page(monkeypatch: pytest.MonkeyPatch, run: str) -> None:
    app = run_app(monkeypatch, PLAYBACK / run)

    assert len(app.selectbox) == 0
    assert len(app.table) == 0
    assert any("No alert to show" in c.value for c in app.caption)
