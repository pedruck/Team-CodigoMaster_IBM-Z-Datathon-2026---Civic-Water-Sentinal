import math
from typing import Any

import pytest
from pydantic import ValidationError

from sentinel.schemas.windows import SensorWindow

VALID: dict[str, Any] = {
    "schema_version": "1.0",
    "timestamp": "2019-01-01T00:05:00Z",
    "pressures": {"P1": 50.1, "P2": 49},
    "flows": {"F1": 20.0},
    "tank_levels": {"T1": 3.2},
}


def with_(**changes: Any) -> dict[str, Any]:
    return {**VALID, **changes}


def error_types(exc: ValidationError) -> set[str]:
    return {e["type"] for e in exc.errors()}


def test_valid_payload_is_accepted_and_ints_become_floats() -> None:
    w = SensorWindow.model_validate(VALID)
    assert w.pressures == {"P1": 50.1, "P2": 49.0}
    assert w.timestamp.utcoffset() is not None


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        SensorWindow.model_validate(with_(timestamp="2019-01-01T00:05:00"))
    assert "timezone_aware" in error_types(exc.value)


def test_offset_timestamp_is_normalised_to_utc() -> None:
    w = SensorWindow.model_validate(with_(timestamp="2019-01-01T02:05:00+02:00"))
    assert w.timestamp.isoformat() == "2019-01-01T00:05:00+00:00"


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_values_are_rejected(bad: float) -> None:
    with pytest.raises(ValidationError) as exc:
        SensorWindow.model_validate(with_(pressures={"P1": bad}))
    assert "finite_number" in error_types(exc.value)


@pytest.mark.parametrize("bad", ["50.1", True, None, [1.0]])
def test_non_numeric_values_are_rejected(bad: Any) -> None:
    with pytest.raises(ValidationError):
        SensorWindow.model_validate(with_(flows={"F1": bad}))


def test_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        SensorWindow.model_validate(with_(leak_label=1))
    assert "extra_forbidden" in error_types(exc.value)


@pytest.mark.parametrize("bad_id", ["", " ", "P 1", "x" * 65])
def test_invalid_sensor_ids_are_rejected(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        SensorWindow.model_validate(with_(pressures={bad_id: 1.0}))


def test_empty_pressures_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SensorWindow.model_validate(with_(pressures={}))


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SensorWindow.model_validate(with_(schema_version="2.0"))


def test_missing_schema_version_is_rejected() -> None:
    payload = dict(VALID)
    del payload["schema_version"]
    with pytest.raises(ValidationError):
        SensorWindow.model_validate(payload)


def test_content_hash_ignores_source_but_not_values() -> None:
    a = SensorWindow.model_validate(with_(source="run-a"))
    b = SensorWindow.model_validate(with_(source="run-b"))
    c = SensorWindow.model_validate(with_(pressures={"P1": 50.2, "P2": 49}))
    assert a.content_hash() == b.content_hash()
    assert a.content_hash() != c.content_hash()
