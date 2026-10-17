"""The dashboard on alerts of the real CUSUM detector (ADR-007), not the mock.

``fixture-cusum`` comes from ``CusumDetector`` with an invented synthetic calibration and an
invented region mapping (so ``is_mock`` stays true). It fills the fields the mock always leaves
empty: ``region``, ``cusum``, ``since`` per sensor and a ``suspected_onset`` before the alert.
Expected values are literals read off that fixture (alert 17:05, onset 16:35, last window
23:55, S 9.393 > h 8.0, sensors n1/n4/n31 in R-SYN-A).
"""

from pathlib import Path
from typing import Any

import pytest

from dashboard.source import AlertQuery, PlaybackAlertSource

testing = pytest.importorskip("streamlit.testing.v1")

RUN = Path(__file__).resolve().parents[1] / "dashboard" / "playback" / "fixture-cusum"
ALERT = "A-20190314T1705Z-1"


def run_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SENTINEL_DASHBOARD_PLAYBACK_DIR", str(RUN))
    app = testing.AppTest.from_file("../dashboard/app.py").run()
    assert not app.exception
    return app


def table(app: Any, column: str) -> list[dict[str, Any]]:
    for element in [*app.dataframe, *app.table]:
        frame = element.value
        if column in frame.columns:
            return list(frame.to_dict("records"))
    raise AssertionError(f"no table with column {column!r}")


def test_list_row_shows_region_onset_and_simulated_age(monkeypatch: pytest.MonkeyPatch) -> None:
    (row,) = [r for r in table(run_app(monkeypatch), "Age (simulated)") if r["Alert"] == ALERT]

    assert row["Region"] == "R-SYN-A"
    assert row["Sensors"] == 3
    assert row["Estimated onset"] == "2019-03-14 16:35 UTC"
    assert row["Age (simulated)"] == "6 h 50 min"
    assert row["z"] == "2.05"
    assert row["Mock"] == "SIMULATED"


def test_detail_shows_cusum_evidence_as_received(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch)
    facts = {r["Evidence"]: r["Value"] for r in table(app, "Evidence")}

    assert facts["Region"] == "R-SYN-A"
    assert facts["CUSUM statistic vs threshold"] == "9.393 vs threshold 8.0"
    assert facts["Estimated onset"] == "2019-03-14 16:35 UTC"
    assert facts["Hours since onset (backend)"] == "0.5"
    assert facts["Detector"] == "cusum 0.1.0+036e55e6 (mock)"
    sensors = table(app, "Deviation (m)")
    assert [s["Sensor"] for s in sensors] == ["n1", "n4", "n31"]
    assert {s["Since"] for s in sensors} == {"2019-03-14 16:35 UTC"}


def test_header_flags_the_synthetic_calibration_as_simulated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = run_app(monkeypatch)

    assert any("SIMULATED" in w.value for w in app.warning)
    assert {m.label: m.value for m in app.metric}["Detector"] == "cusum 0.1.0+036e55e6 (mock)"


def test_region_filter_matches_the_detector_region() -> None:
    source = PlaybackAlertSource(RUN)

    assert source.list_alerts(AlertQuery(region="R-SYN-A")).total == 1
    assert source.list_alerts(AlertQuery(region="R-SYN-B")).total == 0
