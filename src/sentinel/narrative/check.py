"""Validation of a generated explanation against its alert. Narrative owner (P5).

Rule (design document §6): every sensor/region/ID, number, date and time in the text must
come from the alert payload. Anything else is rejected and the deterministic template is
used instead. Numbers match after rounding to the decimals written in the text, and by
absolute value ("5,1 m abaixo" restates a deviation of -5.11 m). Integers written in the
text (counts, years, days) are matched the same way, so the check is strict but not
pedantic about rounding.
"""

import re
from collections.abc import Iterator
from datetime import UTC, date
from typing import Any

from sentinel.narrative.facts import alert_facts
from sentinel.schemas.alerts import Alert
from sentinel.schemas.common import ContractModel

_A = re.ASCII
_ISO = re.compile(
    r"(?<!\d)(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]00:?00)?)?(?!\d)",
    _A,
)
_DMY = re.compile(r"(?<!\d)(\d{2})/(\d{2})/(\d{4})(?!\d)", _A)
_TIME = re.compile(r"(?<![\d:])(\d{1,2})(?::|h)(\d{2})(?::\d{2})?(?![\d:])", _A)
_ID = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z_-]*\d[A-Za-z0-9_-]*", _A)
_Z_SCORE = re.compile(r"[zZ]-\d", _A)
_SIGNS = "-\u2212\u2013+"  # hyphen, minus sign, en dash, plus
_NUMBER = re.compile(rf"(?<![A-Za-z0-9_.,])[{_SIGNS}]?\d+(?:[.,]\d+)?(?!\d)", _A)
_FORBIDDEN = re.compile(
    r"vazamento confirmado|confirmamos|h[áa] um vazamento|existe um vazamento"
    r"|n[ãa]o h[áa] vazamento|com certeza|sem d[úu]vida"
    r"|confirmed leak|leak (?:is )?confirmed|there is (?:a|no) leak|\bno leak\b"
    r"|\bcertainly\b|\bdefinitely\b",
    re.IGNORECASE,
)
_MOCK_LABEL = re.compile(r"simula|mock", re.IGNORECASE)


class NarrativeCheck(ContractModel):
    ok: bool
    unknown_references: tuple[str, ...] = ()
    unknown_numbers: tuple[str, ...] = ()
    unknown_datetimes: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()
    missing_mock_label: bool = False


def _numbers(value: Any) -> Iterator[float]:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int | float):
        yield float(value)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _numbers(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _numbers(v)


def _parse_number(token: str) -> tuple[float, int]:
    digits = token.lstrip(_SIGNS).replace(",", ".")
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return float(digits), decimals


class _Allowed:
    def __init__(self, alert: Alert) -> None:
        facts = alert_facts(alert)
        models = (alert.models.forecaster, alert.models.detector)
        literals = {alert.alert_id, *(d.sensor for d in alert.deviating_sensors)}
        literals |= {m.name for m in models} | {m.version for m in models}
        literals |= {f"{m.name}@{m.version}" for m in models}
        if alert.region:
            literals.add(alert.region)
        for sensor, features in (alert.forecast_contributions_m or {}).items():
            literals.add(sensor)
            literals.update(features)
        numbers = set(_numbers(facts))
        numbers |= {
            abs(v) for f in (alert.forecast_contributions_m or {}).values() for v in f.values()
        }
        numbers.add(float(len(alert.deviating_sensors)))
        if alert.summary:  # the detector's own text is part of the payload
            literals.update(_ID.findall(alert.summary))
            numbers |= {_parse_number(n)[0] for n in _NUMBER.findall(alert.summary)}
        stamps = [alert.window_end, alert.suspected_onset]
        stamps += [d.since for d in alert.deviating_sensors]
        moments = [s.astimezone(UTC) for s in stamps if s is not None]
        self.datetimes = {(m.date(), m.hour, m.minute) for m in moments}
        self.dates = {m.date() for m in moments}
        self.times = {(m.hour, m.minute) for m in moments}
        numbers |= {float(d.year) for d in self.dates} | {float(d.day) for d in self.dates}
        self.numbers = {abs(n) for n in numbers}
        # Longest first, so "P1" never eats the start of an allowed "P10".
        self.literals = sorted(literals, key=len, reverse=True)

    def number_ok(self, token: str) -> bool:
        value, decimals = _parse_number(token)
        tolerance = 0.5 * 10**-decimals + 1e-9
        return any(abs(value - n) <= tolerance for n in self.numbers)


def _strip(pattern: re.Pattern[str], text: str, keep: Any) -> tuple[str, list[str]]:
    """Blank out every match; return the text and the matches for which ``keep`` is False."""
    bad: list[str] = []

    def repl(m: re.Match[str]) -> str:
        if not keep(m):
            bad.append(m.group(0))
        return " "

    return pattern.sub(repl, text), bad


def check_narrative(text: str, alert: Alert) -> NarrativeCheck:
    allowed = _Allowed(alert)
    work = text
    for literal in allowed.literals:
        work = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])", " ", work)

    def iso_ok(m: re.Match[str]) -> bool:
        try:
            day = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return False
        if m[4] is None:
            return day in allowed.dates
        return (day, int(m[4]), int(m[5])) in allowed.datetimes

    def dmy_ok(m: re.Match[str]) -> bool:
        try:
            return date(int(m[3]), int(m[2]), int(m[1])) in allowed.dates
        except ValueError:
            return False

    work, bad_iso = _strip(_ISO, work, iso_ok)
    work, bad_dmy = _strip(_DMY, work, dmy_ok)
    work, bad_time = _strip(_TIME, work, lambda m: (int(m[1]), int(m[2])) in allowed.times)
    work, bad_ids = _strip(_ID, work, lambda m: bool(_Z_SCORE.match(m.group(0))))
    _, bad_numbers = _strip(_NUMBER, work, lambda m: allowed.number_ok(m.group(0)))

    forbidden = tuple(m.group(0) for m in _FORBIDDEN.finditer(text))
    missing_mock = alert.is_mock and not _MOCK_LABEL.search(text)
    unknown_dt = (*bad_iso, *bad_dmy, *bad_time)
    return NarrativeCheck(
        ok=not (bad_ids or bad_numbers or unknown_dt or forbidden or missing_mock),
        unknown_references=tuple(bad_ids),
        unknown_numbers=tuple(bad_numbers),
        unknown_datetimes=unknown_dt,
        forbidden_phrases=forbidden,
        missing_mock_label=missing_mock,
    )
