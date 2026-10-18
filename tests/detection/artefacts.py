"""Invented calibration artefacts shared by the detection tests.

Every number here is made up for the tests (sigma, k, h, N, c, h_close, the forecaster identity
and the two hashes); none of them is a result and none comes from data.
"""

from collections.abc import Mapping
from typing import Any

from sentinel.detection.calibration import CalibrationArtefact

FORECASTER_NAME = "test-profile"
FORECASTER_VERSION = "0.1.0"
INVENTED_RESIDUALS_SHA256 = "0123456789abcdef" * 4
INVENTED_FOLDS_SHA256 = "fedcba9876543210" * 4


def artefact_fields(sigma_m: Mapping[str, float], **overrides: Any) -> dict[str, Any]:
    """The JSON fields of an invented artefact. With sigma 0.1 m a 0.5 m drop gives d = 5, so S
    grows by 4.5 per window; cap c*h = 16. ``overrides`` replace (or add) any field."""
    return {
        "provenance": "synthetic",
        "forecaster": {"name": FORECASTER_NAME, "version": FORECASTER_VERSION},
        "residuals_sha256": INVENTED_RESIDUALS_SHA256,
        "folds_sha256": INVENTED_FOLDS_SHA256,
        "sigma_m": dict(sigma_m),
        "k": 0.5,
        "h": 8.0,
        "n_min": 1,
        "c": 2.0,
        "h_close": 4.0,
        **overrides,
    }


def make_artefact(sigma_m: Mapping[str, float], **overrides: Any) -> CalibrationArtefact:
    return CalibrationArtefact.model_validate(artefact_fields(sigma_m, **overrides))
