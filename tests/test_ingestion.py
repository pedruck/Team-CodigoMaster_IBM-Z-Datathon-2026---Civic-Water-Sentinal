from collections.abc import Callable
from typing import Any

import pytest

from sentinel.schemas.windows import SensorWindow
from sentinel.sensors import SensorRegistry
from sentinel.services.errors import WindowRejectedError
from sentinel.services.ingestion import validate_window


def test_matching_window_passes(
    window: Callable[..., SensorWindow], registry: SensorRegistry
) -> None:
    validate_window(window(1), registry, 300, True)


def test_missing_sensor_is_rejected_with_details(
    payload: Callable[..., dict[str, Any]], registry: SensorRegistry
) -> None:
    p = payload(1)
    del p["pressures"]["P2"]
    with pytest.raises(WindowRejectedError) as exc:
        validate_window(SensorWindow.model_validate(p), registry, 300, True)
    assert exc.value.code == "sensor_set_mismatch"
    assert exc.value.details == {"missing_pressures": ["P2"]}


def test_unexpected_sensor_is_rejected(
    payload: Callable[..., dict[str, Any]], registry: SensorRegistry
) -> None:
    p = payload(1)
    p["flows"]["F9"] = 1.0
    with pytest.raises(WindowRejectedError) as exc:
        validate_window(SensorWindow.model_validate(p), registry, 300, True)
    assert exc.value.details == {"unexpected_flows": ["F9"]}


def test_misaligned_timestamp_is_rejected(
    payload: Callable[..., dict[str, Any]], registry: SensorRegistry
) -> None:
    p = payload(1)
    p["timestamp"] = "2019-01-01T00:06:00Z"
    with pytest.raises(WindowRejectedError) as exc:
        validate_window(SensorWindow.model_validate(p), registry, 300, True)
    assert exc.value.code == "timestamp_misaligned"


def test_misaligned_timestamp_is_allowed_when_alignment_disabled(
    payload: Callable[..., dict[str, Any]], registry: SensorRegistry
) -> None:
    p = payload(1)
    p["timestamp"] = "2019-01-01T00:06:00Z"
    validate_window(SensorWindow.model_validate(p), registry, 300, False)
