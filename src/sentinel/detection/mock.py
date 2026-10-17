"""Test double. NOT the CUSUM detector: a stateful threshold on standardised residuals that
only exists to exercise alert plumbing end to end."""

from collections.abc import Mapping
from datetime import datetime

from sentinel.schemas.common import ModelInfo
from sentinel.schemas.detections import (
    AlertCandidate,
    DetectionResult,
    DeviatingSensor,
    SensorResidual,
)


class ThresholdMockDetector:
    """Opens one alert when >= ``min_sensors`` sensors read ``z_threshold`` sigmas below the
    expected value, stays in alarm while the condition holds, returns to normal afterwards.
    No region is estimated."""

    def __init__(self, sigma_m: float = 1.0, z_threshold: float = 3.0, min_sensors: int = 2):
        self._sigma = sigma_m
        self._threshold = z_threshold
        self._min_sensors = min_sensors
        self._alarm_since: datetime | None = None
        self._info = ModelInfo(name="mock-threshold", version="0.1.0", is_mock=True)

    @property
    def info(self) -> ModelInfo:
        return self._info

    @property
    def in_alarm(self) -> bool:
        return self._alarm_since is not None

    def evaluate(
        self,
        observed: Mapping[str, float],
        expected: Mapping[str, float],
        timestamp: datetime,
    ) -> DetectionResult:
        residuals = {
            s: SensorResidual(
                residual_m=observed[s] - expected[s],
                # Q-DET-02: z = (expected - observed) / sigma, positive on a pressure drop.
                z=(expected[s] - observed[s]) / self._sigma,
            )
            for s in expected
        }
        low = [
            DeviatingSensor(sensor=s, deviation_m=r.residual_m, z=r.z)
            for s, r in residuals.items()
            if r.z is not None and r.z >= self._threshold
        ]
        alarm = len(low) >= self._min_sensors
        alerts: tuple[AlertCandidate, ...] = ()
        if alarm and self._alarm_since is None:
            alerts = (
                AlertCandidate(
                    suspected_onset=timestamp,
                    deviating_sensors=tuple(sorted(low, key=lambda d: d.deviation_m)),
                    summary=f"[MOCK] {len(low)} sensors below expected pressure",
                ),
            )
        # State is mutated only after every object above was built successfully (atomicity).
        if alarm:
            self._alarm_since = self._alarm_since or timestamp
        else:
            self._alarm_since = None
        return DetectionResult(
            timestamp=timestamp,
            model=self._info,
            state="alarm" if alarm else ("suspect" if low else "normal"),
            anomaly_score=float(len(low)),
            residuals=residuals,
            alerts=alerts,
            diagnostics={"deviating_count": len(low), "mock": True},
        )
