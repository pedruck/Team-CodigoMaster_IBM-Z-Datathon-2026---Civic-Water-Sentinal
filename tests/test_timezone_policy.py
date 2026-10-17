"""Timestamps without timezone (SENTINEL_NAIVE_TIMESTAMP_TZ, ADR-004)."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.config import ConfigError, Settings
from sentinel.main import create_app
from sentinel.schemas.windows import SensorWindow, SensorWindowIn
from sentinel.services.errors import WindowRejectedError
from sentinel.services.ingestion import normalize_window
from sentinel.services.pipeline import Pipeline

Payload = Callable[..., dict[str, Any]]


def naive(p: dict[str, Any]) -> dict[str, Any]:
    """Same window with the CSV-style timestamp of data/v1 (``YYYY-MM-DD HH:MM:SS``)."""
    return {**p, "timestamp": p["timestamp"].replace("T", " ").removesuffix("Z")}


def test_input_model_accepts_naive_and_space_separated_timestamp(payload: Payload) -> None:
    w = SensorWindowIn.model_validate(naive(payload(1)))
    assert w.timestamp == datetime(2019, 1, 1, 0, 5)
    assert w.timestamp.tzinfo is None


def test_internal_model_still_requires_timezone(payload: Payload) -> None:
    with pytest.raises(ValueError, match="timezone"):
        SensorWindow.model_validate(naive(payload(1)))


def test_naive_timestamp_is_assumed_utc_by_default(payload: Payload) -> None:
    window, assumed = normalize_window(SensorWindowIn.model_validate(naive(payload(1))), "UTC")
    assert isinstance(window, SensorWindow)
    assert window.timestamp == datetime(2019, 1, 1, 0, 5, tzinfo=UTC)
    assert assumed is True


def test_aware_timestamp_is_not_flagged_as_assumed(payload: Payload) -> None:
    window, assumed = normalize_window(SensorWindowIn.model_validate(payload(1)), "UTC")
    assert window.timestamp == datetime(2019, 1, 1, 0, 5, tzinfo=UTC)
    assert assumed is False


def test_offset_timestamp_is_converted_to_utc(payload: Payload) -> None:
    p = {**payload(1), "timestamp": "2018-12-31T21:05:00-03:00"}
    window, assumed = normalize_window(SensorWindowIn.model_validate(p), "reject")
    assert window.timestamp == datetime(2019, 1, 1, 0, 5, tzinfo=UTC)
    assert assumed is False


def test_reject_policy_refuses_naive_timestamp(payload: Payload) -> None:
    with pytest.raises(WindowRejectedError) as exc:
        normalize_window(SensorWindowIn.model_validate(naive(payload(1))), "reject")
    assert exc.value.code == "timestamp_without_timezone"


def test_naive_and_utc_resend_of_same_instant_is_a_duplicate(
    pipeline: Pipeline, payload: Payload
) -> None:
    first = pipeline.ingest(SensorWindowIn.model_validate(naive(payload(0))))
    again = pipeline.ingest(SensorWindowIn.model_validate(payload(0)))
    assert first.created and first.result.timestamp_tz_assumed
    assert not again.created and again.result.duplicate


def test_api_accepts_csv_style_timestamp_and_reports_assumption(
    client: TestClient, payload: Payload
) -> None:
    r = client.post("/v1/windows", json=naive(payload(0)))
    assert r.status_code == 201
    assert r.json()["timestamp"] == "2019-01-01T00:00:00Z"
    assert r.json()["timestamp_tz_assumed"] is True


def test_api_reject_policy_returns_422(
    settings: Settings, pipeline: Pipeline, payload: Payload
) -> None:
    strict = replace(settings, naive_timestamp_tz="reject")
    pipeline.settings = strict
    r = TestClient(create_app(strict, pipeline)).post("/v1/windows", json=naive(payload(0)))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "timestamp_without_timezone"


def test_naive_timezone_setting_is_validated() -> None:
    assert Settings.from_env({}).naive_timestamp_tz == "UTC"
    assert (
        Settings.from_env({"SENTINEL_NAIVE_TIMESTAMP_TZ": "reject"}).naive_timestamp_tz == "reject"
    )
    with pytest.raises(ConfigError):
        Settings.from_env({"SENTINEL_NAIVE_TIMESTAMP_TZ": "America/Sao_Paulo"})
