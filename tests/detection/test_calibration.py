"""Loading the CUSUM calibration artefact (ADR-008 §B; BE-012a/b slices).

The calibration numbers below are invented for the tests; they are not results.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from sentinel.config import ConfigError
from sentinel.detection.calibration import CalibrationArtefact, write_artefact
from sentinel.detection.cusum import CusumDetector
from sentinel.schemas.common import ModelInfo
from tests.conftest import INTERVAL, ts
from tests.detection.artefacts import (
    FORECASTER_NAME,
    FORECASTER_VERSION,
    artefact_fields,
    make_artefact,
)

ARTEFACT: dict[str, Any] = artefact_fields({"P1": 0.1, "P2": 0.2})


def write(tmp_path: Path, artefact: dict[str, Any]) -> Path:
    path = tmp_path / "calibration.synthetic.json"
    path.write_text(json.dumps(artefact), encoding="utf-8")
    return path


def test_detector_uses_the_numbers_of_the_artefact_file(tmp_path: Path) -> None:
    detector = CusumDetector.from_file(write(tmp_path, ARTEFACT), interval=INTERVAL)

    result = detector.evaluate({"P1": 49.5, "P2": 49.5}, {"P1": 50.0, "P2": 50.0}, ts(0))

    # P1: d = 0.5/0.1 = 5 -> S = 4.5; P2: d = 0.5/0.2 = 2.5 -> S = 2.0
    assert {s: r.cusum for s, r in result.residuals.items()} == {"P1": 4.5, "P2": 2.0}


def test_version_and_calibration_id_carry_the_hash_of_the_artefact_file(tmp_path: Path) -> None:
    path = write(tmp_path, ARTEFACT)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    detector = CusumDetector.from_file(path, interval=INTERVAL)

    result = detector.evaluate({"P1": 50.0, "P2": 50.0}, {"P1": 50.0, "P2": 50.0}, ts(0))

    assert detector.info.version == f"0.1.0+{digest[:8]}"
    assert result.model == detector.info
    assert result.diagnostics["calibration_id"] == digest


def test_written_artefact_loads_back_with_the_same_id_as_the_one_in_memory(tmp_path: Path) -> None:
    artefact = make_artefact({"P2": 0.2, "P1": 0.1})  # not sorted: the file order is kept
    path = tmp_path / "calibration.synthetic.json"

    write_artefact(artefact, path)
    in_memory = CusumDetector(artefact, interval=INTERVAL)
    from_file = CusumDetector.from_file(path, interval=INTERVAL)

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    observed = expected = {"P1": 50.0, "P2": 50.0}
    for detector in (in_memory, from_file):
        assert detector.info.version == f"0.1.0+{digest[:8]}"
        assert detector.evaluate(observed, expected, ts(0)).diagnostics["calibration_id"] == digest
    assert list(CalibrationArtefact.model_validate_json(path.read_bytes()).sigma_m) == ["P2", "P1"]


def without(key: str) -> dict[str, Any]:
    return {k: v for k, v in ARTEFACT.items() if k != key}


@pytest.mark.parametrize(
    "artefact",
    [
        pytest.param(without("h"), id="no-numeric-default-for-h"),
        pytest.param(without("k"), id="no-numeric-default-for-k"),
        pytest.param(without("n_min"), id="no-numeric-default-for-n"),
        pytest.param(without("sigma_m"), id="no-numeric-default-for-sigma"),
        pytest.param(without("provenance"), id="provenance-required"),
        pytest.param({**ARTEFACT, "provenance": "final"}, id="unknown-provenance"),
        pytest.param({**ARTEFACT, "sigma_m": {}}, id="no-sensors"),
        pytest.param({**ARTEFACT, "sigma_m": {"P1": 0.0}}, id="sigma-zero"),
        pytest.param({**ARTEFACT, "sigma_m": {"P1": -0.1}}, id="sigma-negative"),
        pytest.param({**ARTEFACT, "sigma_m": {"P1": float("inf")}}, id="sigma-infinite"),
        pytest.param({**ARTEFACT, "k": 0.0}, id="k-zero"),
        pytest.param({**ARTEFACT, "h": 0.0}, id="h-zero"),
        pytest.param({**ARTEFACT, "h": float("nan")}, id="h-nan"),
        pytest.param({**ARTEFACT, "n_min": 0}, id="n-zero"),
        pytest.param({**ARTEFACT, "n_min": True}, id="n-boolean"),
        pytest.param({**ARTEFACT, "n_min": 3}, id="n-above-sensor-count"),
        pytest.param({**ARTEFACT, "cap": 2.0}, id="unknown-field"),
        pytest.param(without("c"), id="no-numeric-default-for-c"),
        pytest.param(without("h_close"), id="no-numeric-default-for-h-close"),
        pytest.param({**ARTEFACT, "c": 1.0}, id="cap-not-above-h"),
        pytest.param({**ARTEFACT, "c": float("inf")}, id="cap-infinite"),
        pytest.param({**ARTEFACT, "h_close": 0.0}, id="h-close-zero"),
        pytest.param({**ARTEFACT, "h_close": 8.5}, id="h-close-above-h"),
        pytest.param(without("forecaster"), id="forecaster-identity-required"),
        pytest.param(without("residuals_sha256"), id="residuals-hash-required"),
        pytest.param(without("folds_sha256"), id="folds-hash-required"),
        pytest.param({**ARTEFACT, "forecaster": {"name": "x"}}, id="forecaster-without-version"),
        pytest.param({**ARTEFACT, "forecaster": {"version": "1"}}, id="forecaster-without-name"),
        pytest.param(
            {**ARTEFACT, "forecaster": {"name": "x", "version": "1", "id": "y"}},
            id="forecaster-unknown-field",
        ),
        pytest.param(
            {**ARTEFACT, "forecaster": {"name": "", "version": "1"}}, id="forecaster-name-empty"
        ),
        pytest.param(
            {**ARTEFACT, "forecaster": {"name": "x", "version": "v" * 33}},
            id="forecaster-version-too-long-for-model-info",
        ),
        pytest.param({**ARTEFACT, "residuals_sha256": "ab" * 31}, id="residuals-hash-too-short"),
        pytest.param({**ARTEFACT, "residuals_sha256": "ab" * 33}, id="residuals-hash-too-long"),
        pytest.param({**ARTEFACT, "folds_sha256": "AB" * 32}, id="folds-hash-uppercase"),
        pytest.param({**ARTEFACT, "folds_sha256": "xy" * 32}, id="folds-hash-not-hex"),
        pytest.param({**ARTEFACT, "folds_sha256": "ab" * 32 + "\n"}, id="folds-hash-newline"),
        pytest.param({**ARTEFACT, "folds_sha256": 12345}, id="folds-hash-not-a-string"),
    ],
)
def test_invalid_artefact_is_rejected_at_load(tmp_path: Path, artefact: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CusumDetector.from_file(write(tmp_path, artefact), interval=INTERVAL)


@pytest.mark.parametrize("interval", [timedelta(0), -INTERVAL], ids=["zero", "negative"])
def test_window_length_must_be_positive(tmp_path: Path, interval: timedelta) -> None:
    with pytest.raises(ValueError, match="interval"):
        CusumDetector.from_file(write(tmp_path, ARTEFACT), interval=interval)


# ------------------------------------------------------------------ startup checks (ADR-008 §B)

FORECASTER = ModelInfo(name=FORECASTER_NAME, version=FORECASTER_VERSION, is_mock=True)


def for_pipeline(
    tmp_path: Path,
    pressure_sensors: Sequence[str] = ("P1", "P2"),
    forecaster: ModelInfo = FORECASTER,
) -> CusumDetector:
    return CusumDetector.for_pipeline(
        write(tmp_path, ARTEFACT),
        interval=INTERVAL,
        pressure_sensors=pressure_sensors,
        forecaster=forecaster,
    )


def test_artefact_that_matches_the_registry_and_the_forecaster_starts(tmp_path: Path) -> None:
    detector = for_pipeline(tmp_path)

    result = detector.evaluate({"P1": 49.5, "P2": 49.5}, {"P1": 50.0, "P2": 50.0}, ts(0))

    assert {s: r.cusum for s, r in result.residuals.items()} == {"P1": 4.5, "P2": 2.0}


@pytest.mark.parametrize(
    ("sensors", "message"),
    [
        pytest.param(("P1",), "unexpected=.*P2", id="artefact-has-an-extra-sensor"),
        pytest.param(("P1", "P2", "P3"), "missing=.*P3", id="artefact-lacks-a-sensor"),
        pytest.param(("P1", "P3"), "missing=.*P3.*unexpected=.*P2", id="different-sensor"),
        pytest.param(("P2", "P1"), "order", id="same-sensors-in-another-order"),
    ],
)
def test_sensors_other_than_the_registry_pressure_sensors_fail_startup(
    tmp_path: Path, sensors: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        for_pipeline(tmp_path, pressure_sensors=sensors)


@pytest.mark.parametrize(
    ("name", "version"),
    [("other-forecaster", FORECASTER_VERSION), (FORECASTER_NAME, "0.2.0")],
    ids=["other-name", "other-version"],
)
def test_forecaster_other_than_the_calibrated_one_fails_startup(
    tmp_path: Path, name: str, version: str
) -> None:
    served = ModelInfo(name=name, version=version, is_mock=True)

    with pytest.raises(
        ConfigError, match=f"{FORECASTER_NAME} {FORECASTER_VERSION}.*{name} {version}"
    ):
        for_pipeline(tmp_path, forecaster=served)
