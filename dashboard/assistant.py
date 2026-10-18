"""Sentinel assistant (BE-029): the alerts explained as a chat, in plain words.

Two parts, like a conversation with the operator:

- an **overview** of what the source is serving: sensors watched, the latest simulated window,
  how many alerts are stored, where the recent ones concentrate and which models produced them;
- the **selected alert**, step by step: what happened, where to look, since when, how strong,
  which sensors, and the audited explanation the backend stored for it (Q-AUD-07).

Every number, ID and time comes from the received contract values (``StatusResponse``,
``Alert``, ``ExplanationStatus``). Nothing is inferred: the page only counts, sorts and words
what it received (ADR-009). The wording is deterministic; when the stored explanation was
written by a language model, its text is shown as such, never mixed with these sentences.
Rendering only: every value is escaped here.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Literal

from dashboard.live import MONTHS, signed_m
from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import ExplanationStatus, StatusResponse

Role = Literal["assistant", "operator", "day"]

TOP_REGIONS = 3
TOP_SENSORS = 6
DISCLAIMER = (
    "An alert is a suspicion raised by the detector for the team to check, "
    "not a confirmed leak. The data is simulated (BattLeDIM L-Town)."
)


@dataclass(frozen=True)
class Message:
    role: Role
    html: str  # markup built here from escaped values
    time: str | None = None  # simulated clock of the message, e.g. "08:15"


def _b(text: str) -> str:
    return f"<strong>{escape(text)}</strong>"


def day_label(value: datetime) -> str:
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}"


def when_label(value: datetime) -> str:
    """``31 Dec, 08:15`` (UTC, as every time on the page)."""
    return f"{value.day} {MONTHS[value.month - 1]}, {value:%H:%M}"


def _clock(value: datetime | None) -> str | None:
    return f"{value:%H:%M}" if value else None


def _number(value: float) -> str:
    return f"{value:.1f}" if float(value).is_integer() else str(round(value, 2))


def _age(hours: float) -> str:
    """The backend's ``hours_since_onset`` in words: days once it is over two days."""
    if hours >= 48:
        return f"about {hours / 24:.0f} days ({hours:.0f} h)"
    if hours >= 1:
        return f"about {hours:.0f} h"
    return f"{hours * 60:.0f} min"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ------------------------------------------------------------------------------ overview


def overview_messages(
    status: StatusResponse | None, alerts: Sequence[Alert], *, recording: bool = False
) -> list[Message]:
    """The situation as the source reports it. ``alerts``: the recent ones the queue shows."""
    now = _clock(status.last_timestamp) if status else None
    out = [Message("operator", "Hi! What's the situation right now?", now)]
    if status is None:
        out.append(
            Message(
                "assistant",
                "I can't reach the alert source at the moment, so I have nothing new to report. "
                "The map still shows the network; I'll pick up as soon as the source answers.",
            )
        )
        return out

    sensors = status.sensor_registry.get("pressure")
    watching = (
        f"I'm watching {_b(f'{sensors} pressure sensors')} of L-Town"
        if isinstance(sensors, int)
        else "I'm watching the pressure sensors of L-Town"
    )
    latest = (
        f" The latest 5-minute window I processed ends on "
        f"{_b(when_label(status.last_timestamp))} {status.last_timestamp.year} (simulated clock)."
        if status.last_timestamp
        else " No window has been processed yet."
    )
    source = " This is a recording, not live data." if recording else ""
    out.append(Message("assistant", f"{watching}, a simulated water network.{latest}{source}", now))

    stored = status.counters.get("alerts_stored")
    if not alerts:
        out.append(
            Message(
                "assistant",
                "No alert has been raised in this period. When the detector raises one, "
                "it appears in the queue and I explain it here.",
                now,
            )
        )
    else:
        newest, oldest = alerts[0], alerts[-1]
        regions = Counter(a.region for a in alerts if a.region).most_common(TOP_REGIONS)
        where = ", ".join(f"{_b(r)} ({n})" for r, n in regions) or "regions not estimated"
        total = f"{_b(str(stored))} alerts are stored. " if stored is not None else ""
        out.append(
            Message(
                "assistant",
                f"{total}The {_plural(len(alerts), 'most recent alert')} "
                f"(from {escape(when_label(oldest.window_end))} to "
                f"{escape(when_label(newest.window_end))}) are mostly in {where}.",
                now,
            )
        )
        strongest = max(
            ((abs(d.deviation_m), d, a) for a in alerts for d in a.deviating_sensors),
            key=lambda item: item[0],
            default=None,
        )
        if strongest is not None:
            _, dev, alert = strongest
            region = f" in {_b(alert.region)}" if alert.region else ""
            out.append(
                Message(
                    "assistant",
                    f"The biggest pressure drop among them: {_b(signed_m(dev.deviation_m))} at "
                    f"sensor {_b(dev.sensor)}{region}, on {escape(when_label(alert.window_end))}.",
                    now,
                )
            )

    f, d = status.forecaster, status.detector
    models = (
        f"Behind me: the forecaster {_b(f.name)} predicts each sensor's pressure, and the "
        f"detector {_b(d.name)} raises an alert when readings stay below the forecast."
    )
    if status.mocks_active:
        models += (
            " ⚠️ Some components are mocks or not finally calibrated: nothing here is "
            "leak detection."
        )
    out.append(Message("assistant", models, now))
    out.append(
        Message(
            "assistant",
            "Pick an alert in the queue at the top and I'll walk you through it here. "
            f"{escape(DISCLAIMER)}",
            now,
        )
    )
    return out


# ------------------------------------------------------------------------------ one alert


def _sensor_list(alert: Alert) -> str:
    ranked = sorted(alert.deviating_sensors, key=lambda d: -abs(d.deviation_m))
    rows = "".join(
        f"<li><code>{escape(d.sensor)}</code><span>{escape(signed_m(d.deviation_m))}</span></li>"
        for d in ranked[:TOP_SENSORS]
    )
    more = len(ranked) - TOP_SENSORS
    tail = f"<li class='more'>+{more} more in the evidence panel</li>" if more > 0 else ""
    return f'<ul class="chat-sensors">{rows}{tail}</ul>'


def _audited(alert: Alert, explanation: ExplanationStatus | None, note: str | None) -> Message:
    if explanation is None or explanation.alert_id != alert.alert_id:
        return Message(
            "assistant",
            "This source has no stored explanation for the alert (a recording, a snapshot or an "
            "API without the audit chain), so everything above comes straight from the alert's "
            "own fields.",
            _clock(alert.window_end),
        )
    seq = f"record seq {explanation.record.seq}" if explanation.record else "no record yet"
    label = "Written by a language model" if explanation.source == "llm" else "Audited text"
    summary = f"{label} · {seq}"
    body = f'<p class="chat-audited-text">{escape(explanation.text)}</p>' + (
        f'<p class="chat-audited-note">{escape(note)}</p>' if note else ""
    )
    opened = " open" if explanation.source == "llm" else ""
    return Message(
        "assistant",
        "For the record, this is the explanation the backend stored and chained in its audit "
        f'log:<details class="chat-audited"{opened}><summary>{escape(summary)}</summary>'
        f"{body}</details>",
        _clock(alert.window_end),
    )


def alert_messages(
    alert: Alert,
    explanation: ExplanationStatus | None = None,
    explanation_note: str | None = None,
) -> list[Message]:
    """The selected alert, one idea per bubble."""
    t = _clock(alert.window_end)
    region = alert.region
    place = f"region {region}" if region else "an unidentified region"
    out = [
        Message("day", escape(day_label(alert.window_end))),
        Message(
            "operator",
            f"What happened in {escape(place)} at {escape(t or '')}?",
            t,
        ),
    ]
    if alert.is_mock:
        out.append(
            Message(
                "assistant",
                "⚠️ Heads-up: this alert came from mock components. It shows how an alert looks, "
                "but it is not a real detection.",
                t,
            )
        )
    n = len(alert.deviating_sensors)
    if n:
        strongest = max(alert.deviating_sensors, key=lambda d: abs(d.deviation_m))
        headline = (
            f"Pressure in {_b(place)} fell below what the forecast expected on "
            f"{_b(_plural(n, 'sensor'))} at once; the largest gap is "
            f"{_b(signed_m(strongest.deviation_m))} at {_b(strongest.sensor)}. "
        )
    else:
        headline = f"The detector flagged {_b(place)}, but listed no deviating sensor. "
    out.append(
        Message(
            "assistant",
            headline + "A drop like this, on several sensors together, is the typical sign of a "
            f"leak, so the detector raised alert <code>{escape(alert.alert_id)}</code>. "
            "It is a suspicion to check, not a confirmed leak.",
            t,
        )
    )
    if alert.candidate_pipe:
        where = (
            f"📍 <b>Where to look:</b> pipe {_b(alert.candidate_pipe)} is the most likely "
            f"spot{f' in {escape(region)}' if region else ''}. It is a best guess from the "
            "network layout, not a confirmed location."
        )
    else:
        where = (
            "📍 <b>Where to look:</b> the detector did not name a candidate pipe"
            f"{f'; start with region {_b(region)}' if region else ''}."
        )
    out.append(Message("assistant", where, t))
    if alert.suspected_onset:
        since = (
            "⏱️ <b>Since when:</b> the drop probably started on "
            f"{_b(when_label(alert.suspected_onset))}"
        )
        if alert.hours_since_onset is not None:
            since += f", {escape(_age(alert.hours_since_onset))} before this alert"
        out.append(Message("assistant", since + ".", t))
    if alert.cusum:
        out.append(
            Message(
                "assistant",
                "📈 <b>How strong:</b> the detector's score reached "
                f"{_b(_number(alert.cusum.statistic))}, above its alarm threshold of "
                f"{_b(_number(alert.cusum.threshold))}.",
                t,
            )
        )
    if n:
        out.append(
            Message(
                "assistant",
                "📉 <b>Sensors below the forecast</b> (observed minus expected):"
                + _sensor_list(alert),
                t,
            )
        )
    out.append(
        Message(
            "assistant",
            "👉 <b>Next step</b> (the operator's call): check the area"
            f"{f' around pipe {_b(alert.candidate_pipe)}' if alert.candidate_pipe else ''}"
            f"{f' in {_b(region)}' if region else ''} and compare with field reports. "
            "The full numbers and the audit reference are in the evidence panel.",
            t,
        )
    )
    out.append(_audited(alert, explanation, explanation_note))
    return out


# ------------------------------------------------------------------------------ markup


def _bubble(m: Message) -> str:
    if m.role == "day":
        return f'<li class="chat-day"><span>{m.html}</span></li>'
    who = "You" if m.role == "operator" else "Sentinel"
    time = f"<time>{escape(m.time)}</time>" if m.time else ""
    ticks = '<span class="chat-ticks" aria-hidden="true">✓✓</span>' if m.role == "operator" else ""
    return (
        f'<li class="chat-msg chat-{m.role}"><span class="sr-only">{who}: </span>'
        f'<div class="chat-bubble">{m.html}<span class="chat-meta">{time}{ticks}</span></div></li>'
    )


def chat_html(messages: Sequence[Message], *, title: str, subtitle: str, status_label: str) -> str:
    """One conversation panel: a header like a messaging app and the bubbles."""
    bubbles = "".join(_bubble(m) for m in messages)
    return (
        '<section class="chat" aria-label="' + escape(title) + '">'
        '<header class="chat-head"><span class="chat-avatar" aria-hidden="true">S</span>'
        f"<div><strong>{escape(title)}</strong><small>{escape(subtitle)}</small></div>"
        f'<span class="chat-status">{escape(status_label)}</span></header>'
        f'<ol class="chat-body" role="log" aria-live="polite">{bubbles}</ol></section>'
    )
