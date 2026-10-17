"""The JSON examples in docs/examples are part of the contract documentation: keep them valid."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.config import Settings
from sentinel.main import create_app
from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import WindowResult
from sentinel.schemas.detections import DetectionResult
from sentinel.schemas.forecasts import ForecastResult

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def app_client() -> TestClient:
    return TestClient(create_app(Settings()))  # provisional registry, like a dev server


def test_valid_window_example_is_accepted(app_client: TestClient) -> None:
    assert app_client.post("/v1/windows", json=load("window.valid.json")).status_code == 201


def test_naive_timestamp_example_is_read_as_utc_by_default(app_client: TestClient) -> None:
    r = app_client.post("/v1/windows", json=load("window.naive-timestamp.json"))
    assert r.status_code == 201
    assert r.json()["timestamp_tz_assumed"] is True
    assert r.json()["timestamp"].endswith("Z")


def test_naive_timestamp_example_is_rejected_with_reject_policy() -> None:
    client = TestClient(create_app(Settings(naive_timestamp_tz="reject")))
    r = client.post("/v1/windows", json=load("window.naive-timestamp.json"))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "timestamp_without_timezone"


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("window.invalid.string-value.json", "validation_error"),
        ("window.invalid.unknown-field.json", "validation_error"),
        ("window.invalid.missing-sensor.json", "sensor_set_mismatch"),
        ("window.invalid.misaligned.json", "timestamp_misaligned"),
    ],
)
def test_invalid_window_examples_are_rejected(app_client: TestClient, name: str, code: str) -> None:
    r = app_client.post("/v1/windows", json=load(name))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == code


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("alert.mock.json", Alert),
        ("window-result.scored.json", WindowResult),
        ("forecast-result.valid.json", ForecastResult),
        ("detection-result.valid.json", DetectionResult),
    ],
)
def test_output_examples_match_their_models(name: str, model: Any) -> None:
    model.model_validate(load(name))
