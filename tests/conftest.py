from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.audit.sinks import AuditSink, NullAuditSink
from sentinel.config import Settings
from sentinel.detection.interface import Detector
from sentinel.detection.mock import ThresholdMockDetector
from sentinel.forecasting.interface import Forecaster
from sentinel.forecasting.mock import PersistenceMockForecaster
from sentinel.main import build_pipeline, create_app
from sentinel.schemas.windows import SensorWindow
from sentinel.sensors import SensorRegistry
from sentinel.services.pipeline import Pipeline
from sentinel.synthetic import window_payload

T0 = datetime(2019, 1, 1, tzinfo=UTC)
INTERVAL = timedelta(minutes=5)


def ts(i: int) -> datetime:
    return T0 + i * INTERVAL


@pytest.fixture
def registry() -> SensorRegistry:
    return SensorRegistry(
        pressure=("P1", "P2", "P3"),
        flow=("F1",),
        tank_level=("T1",),
        provisional=True,
        source="tests",
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(history_capacity=6, min_history_windows=3, result_capacity=50)


@pytest.fixture
def payload(registry: SensorRegistry) -> Callable[..., dict[str, Any]]:
    def make(i: int, **kwargs: Any) -> dict[str, Any]:
        return window_payload(ts(i), registry, **kwargs)

    return make


@pytest.fixture
def window(payload: Callable[..., dict[str, Any]]) -> Callable[..., SensorWindow]:
    def make(i: int, **kwargs: Any) -> SensorWindow:
        return SensorWindow.model_validate(payload(i, **kwargs))

    return make


@pytest.fixture
def make_pipeline(settings: Settings, registry: SensorRegistry) -> Callable[..., Pipeline]:
    def make(
        forecaster: Forecaster | None = None,
        detector: Detector | None = None,
        audit_sink: AuditSink | None = None,
        settings_override: Settings | None = None,
    ) -> Pipeline:
        return build_pipeline(
            settings_override or settings,
            registry=registry,
            forecaster=forecaster or PersistenceMockForecaster(),
            detector=detector or ThresholdMockDetector(sigma_m=1.0, z_threshold=3.0, min_sensors=2),
            audit_sink=audit_sink or NullAuditSink(),
        )

    return make


@pytest.fixture
def pipeline(make_pipeline: Callable[..., Pipeline]) -> Pipeline:
    return make_pipeline()


@pytest.fixture
def client(settings: Settings, pipeline: Pipeline) -> TestClient:
    return TestClient(create_app(settings, pipeline))
