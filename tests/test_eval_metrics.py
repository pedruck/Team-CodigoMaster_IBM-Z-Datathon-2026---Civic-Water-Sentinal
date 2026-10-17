"""Layer-2 metrics (ADR-010) on a hand-made worked example.

Leaks (invented, not BattLeDIM):
  p1 abrupt     2019-02-01 00:00 → 2019-02-20 00:00
  p2 incipient  2018-06-01 00:00 → 2019-12-31 23:55   (inherited: active at the start of 2019)
  p3 abrupt     2019-06-01 00:00 → 2019-06-03 00:00
Alerts (window_end; suspected_onset):
  a1 2019-01-31 23:55 (none)        before p1; inside p2's official window
  a2 2019-02-01 06:00 (02-01 05:00) inside p1
  a3 2019-02-01 06:05 (none)        second alert for p1
  a4 2019-02-10 00:00 (none)        inside p1's official window, after its 7 days
  a5 2019-06-05 00:00 (none)        after p3's repair
Worked by hand:
  primary  (official window): p2←a1, p1←a2, p3 missed; repeats a3 a4 a5; false alarms 0
  operator (7 days):          p1←a2; false alarms a1 a4 a5; repeat a3
  new in 2019 (p1, p3):       p1←a2; false alarms a1 a5; repeats a3 a4
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from sentinel.eval.metrics import Evaluation, evaluate_run
from sentinel.eval.report import Manifest, render_report
from sentinel.schemas.alerts import Alert
from sentinel.schemas.common import ModelInfo

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "alert.mock.json"
YEAR = datetime(2019, 1, 1, tzinfo=UTC)


def t(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def leaks() -> pd.DataFrame:
    rows = [
        ("p1", "abrupt", "2019-02-01 00:00", "2019-02-20 00:00", "2019-02-01 00:00"),
        ("p2", "incipient", "2018-06-01 00:00", "2019-12-31 23:55", "2018-09-01 00:00"),
        ("p3", "abrupt", "2019-06-01 00:00", "2019-06-03 00:00", "2019-06-01 00:00"),
    ]
    frame = pd.DataFrame(rows, columns=["pipe", "type", "start", "end", "peak"])
    for column in ("start", "end", "peak"):
        frame[column] = pd.to_datetime(frame[column]).dt.tz_localize("UTC")
    return frame


def alert(n: int, end: str, onset: str | None = None) -> Alert:
    base = Alert.model_validate_json(EXAMPLE.read_text())
    window_end = t(end)
    return base.model_copy(
        update={
            "alert_id": f"A-{window_end:%Y%m%dT%H%M}Z-{n}",
            "window_end": window_end,
            "suspected_onset": t(onset) if onset else None,
        }
    )


ALERTS = [
    alert(1, "2019-01-31 23:55"),
    alert(2, "2019-02-01 06:00", "2019-02-01 05:00"),
    alert(3, "2019-02-01 06:05"),
    alert(4, "2019-02-10 00:00"),
    alert(5, "2019-06-05 00:00"),
]


@pytest.fixture
def evaluation() -> Evaluation:
    return evaluate_run(ALERTS, leaks(), period=timedelta(days=365), year_start=YEAR)


def test_primary_view_uses_the_official_window_and_counts_inherited_leaks(
    evaluation: Evaluation,
) -> None:
    primary = evaluation.primary

    assert (primary.detected, primary.total) == (2, 3)
    assert {leak.pipe: leak.detected_at for leak in primary.leaks} == {
        "p1": t("2019-02-01 06:00"),
        "p2": t("2019-01-31 23:55"),
        "p3": None,
    }
    assert (primary.false_alarms, primary.repeat_openings) == (0, 3)


def test_operator_view_is_the_source_of_false_alarms(evaluation: Evaluation) -> None:
    operator = evaluation.operator

    assert (operator.detected, operator.total) == (1, 3)
    assert operator.false_alarm_times == (
        t("2019-01-31 23:55"),
        t("2019-02-10 00:00"),
        t("2019-06-05 00:00"),
    )
    assert operator.repeat_openings == 1
    assert evaluation.false_alarms_per_week == pytest.approx(3 / (365 / 7))


def test_new_in_year_view_drops_inherited_leaks(evaluation: Evaluation) -> None:
    new = evaluation.new_in_year

    assert [leak.pipe for leak in new.leaks] == ["p1", "p3"]
    assert (new.detected, new.false_alarms, new.repeat_openings) == (1, 2, 2)


def test_delays_and_onset_errors_of_the_primary_view(evaluation: Evaluation) -> None:
    assert evaluation.delay_p50 == timedelta(hours=6)
    assert evaluation.delay_p90 == timedelta(days=244, hours=23, minutes=55)
    assert evaluation.delay_max == timedelta(days=244, hours=23, minutes=55)
    # from peak: p1 06:00 - 00:00 = 6 h; p2 2019-01-31 23:55 - 2018-09-01 = 152 d 23:55
    assert evaluation.peak_delay_p50 == timedelta(hours=6)
    assert evaluation.peak_delay_max == timedelta(days=152, hours=23, minutes=55)
    assert evaluation.onset_error == {"p1": timedelta(hours=5), "p2": None, "p3": None}


def test_no_alert_detects_nothing_and_has_no_delay() -> None:
    evaluation = evaluate_run([], leaks(), period=timedelta(days=365), year_start=YEAR)

    assert evaluation.primary.detected == 0
    assert evaluation.delay_p50 is None
    assert evaluation.false_alarms_per_week == 0


# --- report ---------------------------------------------------------------------------------


BASE_MANIFEST = Manifest(
    run_id="eval-test",
    commit="0123abc",
    dirty=False,
    forecaster=ModelInfo(name="xgboost-33", version="0.1.0", is_mock=False),
    detector=ModelInfo(name="cusum", version="0.1.0+deadbeef", is_mock=False),
    registry_provisional=False,
    registry_sha256="c" * 64,
    lock_sha256="d" * 64,
    calibration_id="deadbeef" * 8,
    data_sha256={"windows_2019.jsonl": "a" * 64, "leaks_2019.csv": "b" * 64},
    architecture="s390x",
    python="3.12.3",
)


def manifest(**overrides: Any) -> Manifest:
    return replace(BASE_MANIFEST, **overrides)


def test_report_always_carries_the_wording_header(evaluation: Evaluation) -> None:
    text = render_report(evaluation, manifest())

    assert text.splitlines()[0].startswith("# Layer-2 evaluation")
    assert "simulated benchmark (BattLeDIM L-Town); thresholds fixed on 2018; 2019 run once" in text
    assert "not validated on a real network" in text
    assert "NOT A RESULT" not in text


@pytest.mark.parametrize(
    "overrides",
    [
        {"detector": ModelInfo(name="cusum", version="0.1.0+deadbeef", is_mock=True)},
        {"forecaster": ModelInfo(name="mock-persistence", version="0.1.0", is_mock=True)},
        {"registry_provisional": True},
        {"dirty": True},
    ],
    ids=["mock-detector", "mock-forecaster", "provisional-registry", "dirty-tree"],
)
def test_report_is_watermarked_for_mocks_provisional_ids_or_a_dirty_tree(
    evaluation: Evaluation, overrides: dict[str, object]
) -> None:
    text = render_report(evaluation, manifest(**overrides))

    assert "**NOT A RESULT**" in text.splitlines()[2]


def test_report_lists_every_leak_and_says_where_false_alarms_come_from(
    evaluation: Evaluation,
) -> None:
    text = render_report(evaluation, manifest())

    assert (
        "| p1 | abrupt | 2019-02-01 00:00 | 2019-02-01 06:00 | 6:00:00 | 6:00:00 | 5:00:00 |"
        in text
    )
    assert "| p3 | abrupt | 2019-06-01 00:00 | missed |" in text
    assert "False alarms (7-day view): 3" in text
    assert "0 by construction" in text
    assert "Region accuracy: not computed" in text
    assert "BattLeDIM score: not computed" in text
    assert "deadbeef" * 8 in text
    assert "c" * 64 in text and "d" * 64 in text
    assert "ADR-010 v1" in text


def test_report_lists_false_alarm_times_inherited_leaks_and_the_time_only_caveat(
    evaluation: Evaluation,
) -> None:
    text = render_report(evaluation, manifest())

    assert "False alarm times: 2019-01-31 23:55, 2019-02-10 00:00, 2019-06-05 00:00" in text
    assert "Leaks active before the year started (counted): p2" in text
    assert "time-only" in text
    assert "| Pipe | Type | Start | Detected at | Delay from start | Delay from peak |" in text


def test_new_in_year_view_reports_no_false_alarms_of_its_own(evaluation: Evaluation) -> None:
    section = render_report(evaluation, manifest()).split("## Leaks new in the year")[1]
    section = section.split("##")[0]

    assert "Detected: 1/2" in section
    assert "False alarms:" not in section


def test_a_label_window_longer_than_the_primary_max_delay_is_refused() -> None:
    long = leaks()
    long.loc[1, "start"] = pd.Timestamp("2017-01-01", tz="UTC")  # p2: 3 years

    with pytest.raises(ValueError, match="p2"):
        evaluate_run(ALERTS, long, period=timedelta(days=365), year_start=YEAR)


def test_alerts_that_could_not_be_fetched_watermark_the_report(evaluation: Evaluation) -> None:
    text = render_report(evaluation, manifest(missing_alerts=2, windows=10))

    assert "2 alerts announced but not fetched" in text.splitlines()[2]
    assert "Alerts announced but not fetched: 2." in text
