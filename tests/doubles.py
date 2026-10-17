"""Test doubles for failure paths. Never used outside tests."""

from collections.abc import Mapping
from datetime import datetime

from sentinel.audit.sinks import AuditError
from sentinel.detection.interface import DetectionError
from sentinel.detection.mock import ThresholdMockDetector
from sentinel.forecasting.interface import ForecastError
from sentinel.forecasting.mock import PersistenceMockForecaster
from sentinel.schemas.alerts import AuditEvent, AuditRef
from sentinel.schemas.detections import DetectionResult
from sentinel.schemas.forecasts import ForecastRequest, ForecastResult


class FlakyForecaster(PersistenceMockForecaster):
    """Raises ForecastError for the timestamps in ``fail_at``; records every request."""

    def __init__(self, fail_at: set[datetime] | None = None, exc: Exception | None = None):
        super().__init__()
        self.fail_at = fail_at or set()
        self.exc = exc or ForecastError("model not loaded")
        self.requests: list[ForecastRequest] = []

    def predict(self, request: ForecastRequest) -> ForecastResult:
        self.requests.append(request)
        if request.target_timestamp in self.fail_at:
            raise self.exc
        return super().predict(request)


class PartialForecaster(PersistenceMockForecaster):
    """Violates the contract: omits the last sensor."""

    def predict(self, request: ForecastRequest) -> ForecastResult:
        full = super().predict(request)
        drop = request.pressure_sensor_ids[-1]
        return full.model_copy(
            update={
                "expected_pressures": {
                    k: v for k, v in full.expected_pressures.items() if k != drop
                }
            }
        )


class CountingDetector(ThresholdMockDetector):
    def __init__(self, fail_at: set[datetime] | None = None, **kwargs: float) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]  # kwargs are the mock's floats/ints
        self.fail_at = fail_at or set()
        self.calls: list[datetime] = []

    def evaluate(
        self, observed: Mapping[str, float], expected: Mapping[str, float], timestamp: datetime
    ) -> DetectionResult:
        self.calls.append(timestamp)
        if timestamp in self.fail_at:
            raise DetectionError("detector state unavailable")
        return super().evaluate(observed, expected, timestamp)


class FailingAuditSink:
    name = "failing"
    enabled = True

    def append(self, event: AuditEvent) -> AuditRef:
        raise AuditError("disk full")
