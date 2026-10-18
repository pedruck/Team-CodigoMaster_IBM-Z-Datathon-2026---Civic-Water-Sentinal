"""Sentinel assistant (BE-029): the alerts explained as a chat, an overview and one alert.

Expected values are read off the recorded runs in ``dashboard/playback/`` (fixture-cusum: one
mock alert R-SYN-A; fixture-edge-cases: four alerts, one with every optional field null), never
recomputed beyond what the assistant itself words.
"""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("streamlit")

from dashboard.assistant import (
    TOP_SENSORS,
    Message,
    alert_messages,
    chat_html,
    day_label,
    overview_messages,
    when_label,
)
from dashboard.source import AlertQuery, PlaybackAlertSource
from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import ExplanationStatus

PLAYBACK = Path(__file__).resolve().parents[1] / "dashboard" / "playback"
RUN = PLAYBACK / "fixture-cusum"
EDGE = PLAYBACK / "fixture-edge-cases"


def one_alert(run: Path = RUN, alert_id: str | None = None) -> Alert:
    alerts = PlaybackAlertSource(run).list_alerts(AlertQuery()).items
    return next(a for a in alerts if alert_id in (None, a.alert_id))


def text(messages: list[Message]) -> str:
    return "\n".join(m.html for m in messages)


def stored(alert: Alert, **update: Any) -> ExplanationStatus:
    base = {
        "status": "ready",
        "alert_id": alert.alert_id,
        "language": "en",
        "alert_payload_sha256": "0" * 64,
        "source": "template",
        "text": "Alert: suspected leak.\nCandidate pipe: p1.",
        "generator": None,
        "generated_at": "2026-10-18T05:44:10Z",
        "fallback_reason": None,
        "record": {"seq": 894, "hash": "a" * 64},
    }
    return ExplanationStatus.model_validate({**base, **update})


# ------------------------------------------------------------------------------ overview


def test_overview_states_the_situation_from_the_source() -> None:
    source = PlaybackAlertSource(EDGE)
    status = source.status()
    alerts = source.list_alerts(AlertQuery()).items

    messages = overview_messages(status, alerts)
    said = text(messages)

    assert messages[0].role == "operator"
    assert all(m.role == "assistant" for m in messages[1:])
    sensors = status.sensor_registry["pressure"]
    assert f"<strong>{sensors} pressure sensors</strong>" in said
    assert "a simulated water network" in said
    assert "most recent alerts" in said and "R-FIXTURE" in said
    assert "The biggest pressure drop among them" in said
    assert "not a confirmed leak" in said
    assert "recording" not in said  # only said when the caller says so


def test_overview_warns_about_mocks_and_recordings() -> None:
    source = PlaybackAlertSource(RUN)
    status = source.status()
    assert status.mocks_active

    said = text(overview_messages(status, source.list_alerts(AlertQuery()).items, recording=True))

    assert "This is a recording, not live data." in said
    assert "mocks or not finally calibrated" in said


def test_overview_without_source_or_alerts() -> None:
    offline = overview_messages(None, [])
    assert "can't reach the alert source" in text(offline)

    status = PlaybackAlertSource(RUN).status()
    quiet = text(overview_messages(status, []))
    assert "No alert has been raised in this period" in quiet
    assert "biggest pressure drop" not in quiet


# ------------------------------------------------------------------------------ one alert


def test_alert_conversation_covers_what_where_when_how_strong_and_next_step() -> None:
    alert = one_alert(EDGE, "A-20190314T1640Z-93").model_copy(update={"candidate_pipe": "p201"})
    messages = alert_messages(alert, stored(alert), "Deterministic text from the alert's fields.")
    said = text(messages)

    assert messages[0].role == "day" and messages[0].html == day_label(alert.window_end)
    assert alert.region is not None
    assert messages[1].role == "operator" and alert.region in messages[1].html
    assert {m.time for m in messages[1:]} == {f"{alert.window_end:%H:%M}"}
    assert f"<code>{alert.alert_id}</code>" in said
    assert "not a confirmed leak" in said
    for heading in ("Where to look:", "Since when:", "How strong:", "Next step"):
        assert heading in said
    assert alert.candidate_pipe and f"<strong>{alert.candidate_pipe}</strong>" in said
    assert alert.suspected_onset and when_label(alert.suspected_onset) in said
    assert "Audited text · record seq 894" in said
    assert "Deterministic text from the alert&#x27;s fields." in said
    assert '<details class="chat-audited">' in said  # template text: collapsed


def test_language_model_text_is_labelled_and_open() -> None:
    alert = one_alert()
    said = text(alert_messages(alert, stored(alert, source="llm", generator="granite")))
    assert "Written by a language model · record seq 894" in said
    assert '<details class="chat-audited" open>' in said


def test_an_explanation_of_another_alert_or_none_is_not_shown() -> None:
    alert = one_alert()
    for explanation in (None, stored(alert, alert_id="A-other")):
        said = text(alert_messages(alert, explanation))
        assert "has no stored explanation" in said
        assert "Candidate pipe: p1." not in said


def test_missing_fields_are_said_not_invented() -> None:
    alert = one_alert(EDGE, "A-20190314T1640Z-91")  # every optional field null
    said = text(alert_messages(alert))

    assert "an unidentified region" in said
    assert "did not name a candidate pipe" in said
    assert "Since when:" not in said and "How strong:" not in said


def test_mock_alert_gets_a_heads_up_first() -> None:
    alert = one_alert()
    assert alert.is_mock
    messages = alert_messages(alert)
    assert "mock components" in messages[2].html


def test_sensor_list_is_sorted_and_truncated() -> None:
    alert = one_alert()
    many = alert.model_copy(
        update={
            "deviating_sensors": tuple(
                alert.deviating_sensors[0].model_copy(
                    update={"sensor": f"n{i}", "deviation_m": -0.1 * i}
                )
                for i in range(1, TOP_SENSORS + 3)
            )
        }
    )
    said = text(alert_messages(many))
    assert said.index(f"n{TOP_SENSORS + 2}") < said.index(f"n{TOP_SENSORS + 1}")
    assert "+2 more in the evidence panel" in said
    assert "<code>n1</code>" not in said


def test_values_are_escaped() -> None:
    alert = one_alert().model_copy(
        update={"region": "<script>x</script>", "candidate_pipe": "<b>p1</b>"}
    )
    said = text(alert_messages(alert, stored(alert, text="<img src=x onerror=y>")))
    assert "<script>" not in said and "<b>p1" not in said and "<img" not in said
    assert "&lt;script&gt;" in said and "&lt;img" in said


# ------------------------------------------------------------------------------ markup


def test_chat_markup_is_a_labelled_log_with_bubbles() -> None:
    alert = one_alert()
    html = chat_html(
        alert_messages(alert), title="Alert <R>", subtitle=alert.alert_id, status_label="online"
    )
    assert html.startswith('<section class="chat" aria-label="Alert &lt;R&gt;">')
    assert 'role="log"' in html
    assert html.count('class="chat-msg chat-operator"') == 1
    assert '<span class="sr-only">You: </span>' in html
    assert '<span class="sr-only">Sentinel: </span>' in html
    assert 'class="chat-ticks"' in html
    assert '<ol class="chat-body" role="log" aria-live="polite"></ol>' in chat_html(
        [], title="This alert", subtitle="", status_label="offline"
    )
