"""The committed final calibration of the served forecaster (`config/calibration.xgboost-33.json`,
BE-012h, ADR-008 §G): it loads, it is not a mock, and it matches the registry, the regions and the
forecaster identity the server checks at startup. These tests say nothing about detecting leaks."""

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest

from sentinel.config import ConfigError
from sentinel.detection.calibration import CalibrationArtefact
from sentinel.detection.cusum import CusumDetector
from sentinel.schemas.common import ModelInfo
from sentinel.sensors import SensorRegistry

CONFIG = Path(__file__).parents[2] / "config"
CALIBRATION = CONFIG / "calibration.xgboost-33.json"
# The hash recorded in ADR-008 §G and `docs/p4-calibration.md`: changing the file means a new
# calibration, a new hash in both and a new pre-registered decision, not an edit of this constant.
CALIBRATION_SHA256 = "fad6ec0bded539cd326b6e53f77d7504bdeef68d4800427fcee26acd3b874256"
REGISTRY = SensorRegistry.load(CONFIG / "sensors.battledim.json")
INTERVAL = timedelta(minutes=5)
SERVED = ModelInfo(name="xgboost-33", version="0.1.0", is_mock=False)


def test_the_file_is_the_one_recorded_in_adr_008() -> None:
    assert hashlib.sha256(CALIBRATION.read_bytes()).hexdigest() == CALIBRATION_SHA256


def test_the_artefact_is_final_and_calibrated_for_the_served_forecaster() -> None:
    artefact = CalibrationArtefact.model_validate_json(CALIBRATION.read_text(encoding="utf-8"))

    assert artefact.provenance == "battledim-2018-oof"
    assert not artefact.is_mock
    assert (artefact.forecaster.name, artefact.forecaster.version) == ("xgboost-33", "0.1.0")
    assert tuple(artefact.sigma_m) == REGISTRY.pressure


def test_the_server_starts_the_detector_with_the_official_registry_and_regions() -> None:
    detector = CusumDetector.for_pipeline(
        CALIBRATION,
        interval=INTERVAL,
        pressure_sensors=REGISTRY.pressure,
        forecaster=SERVED,
        regions=CONFIG / "regions.battledim.json",
    )

    assert detector.info.name == "cusum"
    assert detector.info.version.endswith(CALIBRATION_SHA256[:8])
    assert not detector.info.is_mock


@pytest.mark.parametrize(
    "other",
    [
        ModelInfo(name="xgboost-33", version="0.2.0", is_mock=False),
        ModelInfo(name="xgboost-33-without-neighbours", version="candidate", is_mock=False),
        ModelInfo(name="hour-of-week-baseline", version="0.1.0", is_mock=False),
    ],
)
def test_another_forecaster_is_refused(other: ModelInfo) -> None:
    """The calibration holds only for the residuals of the model it was made on."""
    with pytest.raises(ConfigError, match="calibration was made for forecaster"):
        CusumDetector.for_pipeline(
            CALIBRATION, interval=INTERVAL, pressure_sensors=REGISTRY.pressure, forecaster=other
        )
