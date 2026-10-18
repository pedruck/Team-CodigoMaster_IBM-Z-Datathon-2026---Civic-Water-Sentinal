"""The served forecaster names the exact artefact it loaded (digest in the version), a pinned
digest stops startup on any other file, and the CUSUM still matches its calibration."""

import json
from pathlib import Path

import pytest

from sentinel.config import ConfigError, Settings
from sentinel.detection.calibration import write_artefact
from sentinel.forecasting.baseline import SLOTS
from sentinel.forecasting.integrity import artefact_sha256, release, versioned
from sentinel.forecasting.registry import ArtefactMismatchError, load_forecaster
from sentinel.main import build_pipeline
from sentinel.sensors import SensorRegistry
from tests.detection.artefacts import make_artefact

OFFICIAL = Path(__file__).resolve().parents[1] / "config/sensors.battledim.json"


def baseline(path: Path, sensors: tuple[str, ...] = ("n1",), value: float = 1.0) -> Path:
    profile = {f"Pressure_{s}": [value] * SLOTS for s in sensors}
    path.write_text(json.dumps({"version": "0.1.0", "profile": profile}), encoding="utf-8")
    return path


def test_directory_digest_covers_names_and_contents_but_not_hidden_files(tmp_path: Path) -> None:
    model = tmp_path / "xgboost-33@0.1.0"
    model.mkdir()
    (model / "meta.json").write_text("{}")
    (model / "n1.json").write_text("[1]")
    first = artefact_sha256(model)

    (model / ".ipynb_checkpoints").mkdir()
    (model / ".ipynb_checkpoints" / "n1-checkpoint.json").write_text("[9]")
    assert artefact_sha256(model) == first

    (model / "n1.json").write_text("[2]")
    changed = artefact_sha256(model)
    assert changed != first
    (model / "n1.json").write_text("[1]")
    (model / "n1.json").rename(model / "n4.json")
    assert artefact_sha256(model) not in {first, changed}

    with pytest.raises(FileNotFoundError):
        artefact_sha256(tmp_path / "absent")


def test_version_helpers() -> None:
    assert versioned("0.1.0", "1a2b3c4d5e") == "0.1.0+1a2b3c4d"
    assert versioned("0.1.0", None) == "0.1.0"
    assert release("0.1.0+1a2b3c4d") == "0.1.0" == release("0.1.0")


def test_pinned_digest_refuses_another_artefact_before_parsing_it(tmp_path: Path) -> None:
    path = baseline(tmp_path / "baseline.json")
    good = artefact_sha256(path)
    assert load_forecaster("baseline", path, expected_sha256=good).info.version == (
        f"0.1.0+{good[:8]}"
    )

    path.write_text("not even json")  # would fail to parse: the digest check comes first
    with pytest.raises(ArtefactMismatchError, match="expected"):
        load_forecaster("baseline", path, expected_sha256=good)


def test_pin_mismatch_is_a_startup_config_error(tmp_path: Path) -> None:
    path = baseline(tmp_path / "baseline.json", SensorRegistry.load(OFFICIAL).pressure)
    settings = Settings(
        sensor_registry_path=OFFICIAL,
        forecaster="baseline",
        forecaster_path=path,
        forecaster_sha256="0" * 64,
    )
    with pytest.raises(ConfigError, match="SENTINEL_FORECASTER_SHA256"):
        build_pipeline(settings)


@pytest.mark.parametrize(
    ("value", "message"),
    [("ABC", "64 lowercase hex"), ("a" * 63, "64 lowercase hex")],
)
def test_pin_format_is_checked(value: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings(forecaster="baseline", forecaster_path=Path("x"), forecaster_sha256=value)
    with pytest.raises(ConfigError, match="SENTINEL_FORECASTER=mock"):
        Settings(forecaster_sha256="a" * 64)


def _cusum(tmp_path: Path, forecaster_version: str, model: Path) -> Settings:
    pressure = SensorRegistry.load(OFFICIAL).pressure
    calibration = tmp_path / "calibration.json"
    write_artefact(
        make_artefact(
            dict.fromkeys(pressure, 0.1),
            forecaster={"name": "baseline-hourofweek", "version": forecaster_version},
        ),
        calibration,
    )
    return Settings(
        sensor_registry_path=OFFICIAL,
        forecaster="baseline",
        forecaster_path=model,
        detector="cusum",
        detector_calibration_path=calibration,
    )


def test_calibration_naming_the_release_matches_any_artefact_of_it(tmp_path: Path) -> None:
    model = baseline(tmp_path / "b.json", SensorRegistry.load(OFFICIAL).pressure)
    pipeline = build_pipeline(_cusum(tmp_path, "0.1.0", model))
    assert pipeline.forecaster.info.version.startswith("0.1.0+")
    assert pipeline.detector.info.name == "cusum"


def test_calibration_naming_a_digest_matches_only_that_artefact(tmp_path: Path) -> None:
    model = baseline(tmp_path / "b.json", SensorRegistry.load(OFFICIAL).pressure)
    digest = artefact_sha256(model)[:8]
    assert build_pipeline(_cusum(tmp_path, f"0.1.0+{digest}", model)).detector.info.name == "cusum"

    baseline(model, SensorRegistry.load(OFFICIAL).pressure, value=2.0)  # other weights
    with pytest.raises(ConfigError, match="calibration was made for forecaster"):
        build_pipeline(_cusum(tmp_path, f"0.1.0+{digest}", model))
