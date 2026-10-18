"""Invented alert for tests and notebooks while the real detector (region, CUSUM) and the
real forecaster (contributions) are not delivered. Every value here is made up, the model
names say ``example`` and ``is_mock`` is always True. Never use it as a result."""

from datetime import UTC, datetime
from typing import Any

from sentinel.schemas.alerts import Alert, AlertModels, AuditRef
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.detections import CusumEvidence, DeviatingSensor


def mock_alert(**overrides: Any) -> Alert:
    """An alert shaped like the design document example (§6): region, onset, CUSUM,
    three deviating sensors and forecaster contributions. ``overrides`` replace fields."""
    window_end = datetime(2019, 3, 14, 12, 30, tzinfo=UTC)
    onset = datetime(2019, 3, 14, 2, 10, tzinfo=UTC)
    fields: dict[str, Any] = {
        "alert_id": "A-20190314T1230Z-1",
        "window_end": window_end,
        "created_at": datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        "classification": "leak_suspected",
        "region": "R4",
        "suspected_onset": onset,
        "hours_since_onset": round((window_end - onset).total_seconds() / 3600, 2),
        "cusum": CusumEvidence(statistic=12.4, threshold=8.0),
        # Q-DET-02: deviation_m = observed - expected (< 0 on a drop), z > 0 on a drop.
        "deviating_sensors": (
            DeviatingSensor(sensor="P14", deviation_m=-0.91, z=4.1, since=onset),
            DeviatingSensor(sensor="P21", deviation_m=-0.62, z=3.2, since=onset),
            DeviatingSensor(sensor="P09", deviation_m=-0.48, z=2.7, since=None),
        ),
        "forecast_contributions_m": {
            "P14": {"hour_of_week": 0.21, "F01_inflow": -0.12, "P14_lag_1": 0.08, "T01": 0.01},
            "P21": {"hour_of_week": 0.17, "F02_inflow": -0.09},
        },
        "summary": "example: 3 sensors below expected pressure in R4",
        "models": AlertModels(
            forecaster=ModelInfo(name="example-forecaster", version="0.0.0", is_mock=True),
            detector=ModelInfo(name="example-cusum", version="0.0.0", is_mock=True),
        ),
        "is_mock": True,
        "audit": AuditRef(seq=150, hash="0" * 64),
    }
    fields.update(overrides)
    return Alert(**fields)
