"""Explanations (P5): deterministic template, validation of generated text, fallback.

All alerts here are invented (``mock_alert``) or come from the backend mocks; the "LLMs"
are fakes returning fixed strings. Every text is English, like the rest of the project.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from sentinel.audit import HashChainAuditSink, alert_payload_sha256
from sentinel.narrative import (
    FunctionNarrator,
    Narrator,
    alert_facts,
    build_prompt,
    check_narrative,
    explain,
    render_text,
)
from sentinel.narrative.mock import mock_alert
from sentinel.schemas.alerts import Alert
from sentinel.schemas.windows import SensorWindow
from sentinel.services.pipeline import Pipeline

GOOD_EN = (
    "[SIMULATION] Alert A-20190314T1230Z-1 reports a suspected leak in region R4. "
    "Sensors P14 and P21 read 0.91 m and 0.62 m below the expected pressure since "
    "2019-03-14 02:10 UTC, about 10.3 h before the 12:30 UTC window. "
    "The CUSUM statistic reached 12.4 against a threshold of 8.0."
)


def fixed(text: str) -> Narrator:
    return FunctionNarrator("fake-llm@0", lambda prompt: text)


# ------------------------------------------------------------------------------ template


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"region": None, "cusum": None, "suspected_onset": None, "hours_since_onset": None},
        {"forecast_contributions_m": None, "audit": None, "summary": None},
        {"is_mock": False},
    ],
    ids=["full", "no-region-cusum-onset", "no-contrib-audit", "not-mock"],
)
def test_template_passes_its_own_check(overrides: dict[str, object]) -> None:
    alert = mock_alert(**overrides)
    text = render_text(alert)
    check = check_narrative(text, alert)
    assert check.ok, check
    assert alert.alert_id in text
    assert all(d.sensor in text for d in alert.deviating_sensors)


def test_template_content() -> None:
    alert = mock_alert()
    text = render_text(alert)
    assert text.startswith("[SIMULATION]")
    assert "Alert A-20190314T1230Z-1: suspected leak in region R4." in text
    assert "2019-03-14 02:10 UTC, 10.33 h before" in text
    assert "P14 -0.91 m (z 4.10)" in text
    assert "CUSUM 12.40 against threshold 8.00" in text
    assert "P14: hour_of_week +0.21 m, F01_inflow -0.12 m, P14_lag_1 +0.08 m" in text
    assert "T01" not in text  # only the top 3 contributions
    assert "seq 150" in text


def test_explanations_are_english_only() -> None:
    alert = mock_alert()
    e = explain(alert)
    assert e.language == "en" and e.text == render_text(alert)
    with pytest.raises(KeyError):
        render_text(alert, "pt")  # type: ignore[arg-type]  # no other language exists


def test_template_without_mock_has_no_simulation_banner() -> None:
    assert "[SIMULATION]" not in render_text(mock_alert(is_mock=False))


def test_facts_contain_only_alert_data() -> None:
    facts = alert_facts(mock_alert())
    assert facts["region"] == "R4"
    assert facts["suspected_onset"] == "2019-03-14T02:10:00Z"
    assert facts["deviating_sensors"][0] == {
        "sensor": "P14",
        "deviation_m": -0.91,
        "z": 4.1,
        "since": "2019-03-14T02:10:00Z",
    }
    assert facts["top_contributions_m"]["P14"][0] == {"feature": "hour_of_week", "value_m": 0.21}
    assert facts["models"]["detector"] == "example-cusum@0.0.0"
    json.dumps(facts)  # JSON-ready


# --------------------------------------------------------------------------------- check


def test_good_generated_text_is_accepted() -> None:
    assert check_narrative(GOOD_EN, mock_alert()).ok


@pytest.mark.parametrize(
    ("text", "field", "value"),
    [
        (GOOD_EN + " Sensor P07 dropped too.", "unknown_references", "P07"),
        (GOOD_EN + " See sensor P-99.", "unknown_references", "P-99"),
        (GOOD_EN.replace("region R4", "region R2"), "unknown_references", "R2"),
        (GOOD_EN + " The estimated loss is 7.3 L/s.", "unknown_numbers", "7.3"),
        (GOOD_EN + " That is 15% of the inflow.", "unknown_numbers", "15"),
        (GOOD_EN.replace("02:10", "03:15"), "unknown_datetimes", "2019-03-14 03:15"),
        (GOOD_EN.replace("the 12:30 UTC", "the 13:00 UTC"), "unknown_datetimes", "13:00"),
        (GOOD_EN.replace("2019-03-14 02:10", "2019-03-15 02:10"), "unknown_datetimes", None),
        (GOOD_EN + " Since 15/03/2019.", "unknown_datetimes", "15/03/2019"),
        (GOOD_EN + " This is a confirmed leak.", "forbidden_phrases", "confirmed leak"),
        (GOOD_EN + " There is a leak.", "forbidden_phrases", "There is a leak"),
        (GOOD_EN + " It is certainly real.", "forbidden_phrases", "certainly"),
        (GOOD_EN + " It is definitely a leak.", "forbidden_phrases", "definitely"),
    ],
    ids=[
        "sensor-not-deviating",
        "invented-sensor",
        "wrong-region",
        "invented-number",
        "percentage",
        "wrong-datetime",
        "wrong-time",
        "wrong-date",
        "wrong-dmy-date",
        "confirms-leak",
        "asserts-leak",
        "certainty",
        "definitely",
    ],
)
def test_text_outside_the_payload_is_rejected(text: str, field: str, value: str | None) -> None:
    check = check_narrative(text, mock_alert())
    assert not check.ok
    found = getattr(check, field)
    assert found and (value is None or value in found)


def test_mock_alert_text_must_say_it_is_a_simulation() -> None:
    check = check_narrative(GOOD_EN.replace("[SIMULATION] ", ""), mock_alert())
    assert not check.ok and check.missing_mock_label


@pytest.mark.parametrize(
    "number",
    ["-0.91", "\u22120.91", "0.9", "0.91", "1", "4.1", "10", "10.33", "12", "8.0", "3"],
)
def test_rounded_and_unsigned_payload_numbers_are_accepted(number: str) -> None:
    alert = mock_alert(is_mock=False)
    assert check_narrative(f"Value {number} in alert {alert.alert_id}.", alert).ok


def test_rounding_tolerance_follows_the_written_decimals() -> None:
    alert = mock_alert(is_mock=False)
    assert not check_narrative("Deviation of 0.95 m.", alert).ok  # -0.91 is not 0.95
    assert not check_narrative("Deviation of 6 m.", alert).ok


def test_a_decimal_comma_is_not_read_as_a_decimal() -> None:
    """English text: "0,91" is two numbers, and neither is in the alert."""
    check = check_narrative("[SIMULATION] Sensor P14 read 0,91 m below expected.", mock_alert())
    assert not check.ok and check.unknown_numbers


def test_allowed_sensor_does_not_cover_a_longer_id() -> None:
    alert = mock_alert(is_mock=False)
    check = check_narrative("Sensor P140 dropped.", alert)
    assert check.unknown_references == ("P140",)


# ------------------------------------------------------------------------------- explain


def test_without_narrator_the_template_is_used() -> None:
    alert = mock_alert()
    e = explain(alert)
    assert e.source == "template" and e.generator is None and e.fallback_reason is None
    assert e.text == render_text(alert)
    assert e.alert_payload_sha256 == alert_payload_sha256(alert)
    assert e.audit == alert.audit and e.is_mock


def test_valid_generated_text_is_used() -> None:
    e = explain(mock_alert(), fixed(GOOD_EN))
    assert e.source == "llm" and e.text == GOOD_EN and e.generator == "fake-llm@0"
    assert e.language == "en"
    assert e.check is not None and e.check.ok


def test_hallucinated_text_falls_back_to_the_template() -> None:
    alert = mock_alert()
    e = explain(alert, fixed(GOOD_EN + " Sensor P07 also dropped 7.3 m."))
    assert e.source == "template" and e.text == render_text(alert)
    assert e.fallback_reason == "narrative_rejected"
    assert e.check is not None
    assert e.check.unknown_references == ("P07",) and e.check.unknown_numbers == ("7.3",)


def test_model_failure_or_empty_output_falls_back() -> None:
    def broken(prompt: str) -> str:
        raise TimeoutError("model endpoint timed out")

    e = explain(mock_alert(), FunctionNarrator("broken", broken))
    assert e.source == "template" and e.fallback_reason is not None
    assert "TimeoutError" in e.fallback_reason
    e = explain(mock_alert(), fixed("   "))
    assert e.source == "template" and e.fallback_reason == "narrator_empty"


def test_prompt_carries_the_facts_and_the_rules() -> None:
    seen: list[str] = []

    def record(prompt: str) -> str:
        seen.append(prompt)
        return GOOD_EN

    explain(mock_alert(), FunctionNarrator("spy", record))
    (prompt,) = seen
    assert prompt == build_prompt(mock_alert())
    facts = json.loads(prompt[prompt.index("{") :])
    assert facts == alert_facts(mock_alert())
    assert "[SIMULATION]" in prompt and "You do not decide" in prompt
    assert "in English" in prompt and "leak_label" not in prompt


def test_explanation_of_a_real_pipeline_alert_is_tied_to_its_audit_record(
    make_pipeline: Callable[..., Pipeline],
    window: Callable[..., SensorWindow],
) -> None:
    """Backend mocks (no region, no CUSUM) + real audit chain."""
    sink = HashChainAuditSink(fsync=False)
    p = make_pipeline(audit_sink=sink)
    for i in range(5):
        p.ingest(window(i))
    r = p.ingest(window(5, drop_m=5.0, drop_sensors=("P1", "P2"))).result
    alert = p.alerts.get(r.alert_ids[0])
    assert alert is not None and alert.audit is not None

    e = explain(alert)
    record = sink.get(alert.audit.seq)
    assert record is not None
    assert e.alert_payload_sha256 == record["payload_sha256"]
    assert e.audit == alert.audit
    assert check_narrative(e.text, alert).ok
    assert e.text.startswith("[SIMULATION]")


def test_rejected_model_text_is_kept_for_diagnosis() -> None:
    bad = GOOD_EN + " Sensor P07 also dropped 7.3 m."
    e = explain(mock_alert(), fixed(bad))
    assert e.source == "template" and e.model_text == bad
    assert explain(mock_alert()).model_text is None


# ------------------------------------------- real outputs from the team VM (regression cases)
# Granite 4.0 H 350M base, llama-cpp-python 0.3.36 on the LinuxONE, 2026-10-17.

VM_MISATTRIBUTED = (
    "[SIMULATION] Alert A-20190314T1230Z-1 reports a suspected leak in region R4. "
    "Sensors P14 and P21 read 0.21 m and 0.17 m below the expected pressure since "
    "2019-03-14 02:10 UTC. The CUSUM statistic reached 12.4 against a threshold of 8.0. "
    "The top contributions for sensors P14 and P21 are 0.21 m and 0.17 m below the expected "
    "pressure, respectively."
)


def test_vm_text_passing_contributions_off_as_deviations_is_rejected() -> None:
    """Every number exists in the payload; they are attached to the wrong thing."""
    check = check_narrative(VM_MISATTRIBUTED, mock_alert())
    assert not check.ok
    assert check.unknown_numbers == () and check.unknown_references == ()
    assert {"0.21 (P14, P21)", "0.17 (P14, P21)"} <= set(check.misattributed_numbers)


def test_short_summary_with_a_count_is_accepted() -> None:
    alert = mock_alert(summary="[MOCK] 3 sensors below expected pressure")
    assert check_narrative("[MOCK] 3 sensors read below the expected pressure.", alert).ok


def test_contribution_next_to_its_feature_is_accepted() -> None:
    text = "[SIMULATION] Sensor P14 read 0.91 m below expected; hour_of_week contributed 0.21 m."
    assert check_narrative(text, mock_alert()).ok


def test_deviation_of_another_sensor_is_rejected() -> None:
    check = check_narrative("[SIMULATION] Sensor P09 read 0.91 m below expected.", mock_alert())
    assert check.misattributed_numbers == ("0.91 (P09)",)


def test_known_limit_swap_between_named_sensors_passes() -> None:
    """Documented limit: both values belong to sensors named in the same clause."""
    text = "[SIMULATION] Sensors P14 and P21 read 0.62 m and 0.91 m below expected."
    assert check_narrative(text, mock_alert()).ok


# ------------------------------------------------- alert 1.1: candidate pipe (ADR-011)

OFFICIAL_CUSUM_EXAMPLE = Path("docs/examples/alert.cusum-synthetic.json")


def test_candidate_pipe_is_shown_as_a_candidate() -> None:
    alert = mock_alert(candidate_pipe="p147")
    text = render_text(alert)
    assert "Candidate pipe (not a confirmed location): p147." in text
    assert check_narrative(text, alert).ok
    assert alert_facts(alert)["candidate_pipe"] == "p147"
    assert "Candidate pipe" not in render_text(mock_alert())  # absent when null


def test_generated_text_may_name_the_pipe_but_not_another_one() -> None:
    alert = mock_alert(candidate_pipe="p147")
    base = GOOD_EN + " The candidate pipe is p147."
    assert check_narrative(base, alert).ok
    check = check_narrative(base.replace("p147", "p148"), alert)
    assert not check.ok and check.unknown_references == ("p148",)


def test_official_cusum_example_alert_is_explained() -> None:
    """The backend's v1.1 example: official sensor ID, L-Town region, candidate pipe."""
    alert = Alert.model_validate_json(OFFICIAL_CUSUM_EXAMPLE.read_text(encoding="utf-8"))
    text = render_text(alert)
    assert "region Z3-R1" in text and "p147" in text and "n215 -0.50 m (z 5.00)" in text
    assert check_narrative(text, alert).ok
    faithful = (
        f"Alert {alert.alert_id} reports a suspected leak in region Z3-R1. Sensor n215 read "
        "0.5 m below the expected pressure since 2019-01-01 16:35 UTC. The candidate pipe is p147."
    )
    e = explain(alert, fixed(faithful))
    assert e.source == "llm" and e.text.startswith("[SIMULATION] ")


# ------------------------------------------------ review of PR #17 (P1): bare days and years


@pytest.mark.parametrize(
    "sentence",
    [
        "Sensor P14 read 14 m below the expected pressure.",  # 14 is the day of window_end
        "Sensor P14 read 3 m below the expected pressure.",  # 3 sensors, and the month
        "In 2019 the sensor P14 read 0.91 m below expected.",  # a bare year
        "Since March 15 the sensor P14 read 0.91 m below expected.",  # wrong day
        "In April 2019 the sensor P14 read 0.91 m below expected.",  # wrong month
    ],
)
def test_bare_days_years_and_wrong_named_dates_are_rejected(sentence: str) -> None:
    assert not check_narrative(f"[SIMULATION] {sentence}", mock_alert()).ok


@pytest.mark.parametrize(
    "sentence",
    [
        "Sensor P14 read 0.91 m below expected since March 14, 2019.",
        "Since the 14th of March the sensor P14 read 0.91 m below expected.",
        "In March 2019 the sensor P14 read 0.91 m below expected.",
    ],
)
def test_dates_written_with_month_names_are_checked_as_dates(sentence: str) -> None:
    assert check_narrative(f"[SIMULATION] {sentence}", mock_alert()).ok


@pytest.mark.parametrize(
    "sentence",
    [
        "Sensor P14 read 4.1 m below expected.",  # its z-score passed off as metres
        "Sensor P14 read 12.4 m below expected.",  # the CUSUM statistic as metres
        "Sensor P14 read 10.33 m below expected.",  # hours since onset as metres
    ],
)
def test_metres_must_be_a_deviation_or_a_contribution(sentence: str) -> None:
    check = check_narrative(f"[SIMULATION] {sentence}", mock_alert())
    assert not check.ok and check.misattributed_numbers
