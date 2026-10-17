"""Loading the CUSUM calibration artefact (ADR-008 §B; BE-012a slice).

The calibration numbers below are invented for the tests; they are not results.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from sentinel.detection.cusum import CusumDetector
from tests.conftest import ts

ARTEFACT: dict[str, Any] = {
    "provenance": "synthetic",
    "sigma_m": {"P1": 0.1, "P2": 0.2},
    "k": 0.5,
    "h": 8.0,
    "n_min": 1,
}


def write(tmp_path: Path, artefact: dict[str, Any]) -> Path:
    path = tmp_path / "calibration.synthetic.json"
    path.write_text(json.dumps(artefact), encoding="utf-8")
    return path


def test_detector_uses_the_numbers_of_the_artefact_file(tmp_path: Path) -> None:
    detector = CusumDetector.from_file(write(tmp_path, ARTEFACT))

    result = detector.evaluate({"P1": 49.5, "P2": 49.5}, {"P1": 50.0, "P2": 50.0}, ts(0))

    # P1: d = 0.5/0.1 = 5 -> S = 4.5; P2: d = 0.5/0.2 = 2.5 -> S = 2.0
    assert {s: r.cusum for s, r in result.residuals.items()} == {"P1": 4.5, "P2": 2.0}


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
    ],
)
def test_invalid_artefact_is_rejected_at_load(tmp_path: Path, artefact: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CusumDetector.from_file(write(tmp_path, artefact))
