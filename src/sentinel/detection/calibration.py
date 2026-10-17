"""Calibration artefact of the CUSUM detector (ADR-008 §B).

Every number the detector uses comes from this file; none has a default in code. Fields:
``provenance``, the identity of the forecaster the numbers belong to, the hashes of the
residual file and of the fold definition they were estimated from, sigma per sensor, ``k``,
``h``, ``n_min`` (N), ``c`` (cap ``c*h``) and ``h_close``.
"""

import math
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from sentinel.schemas.common import FiniteFloat
from sentinel.schemas.windows import SensorId

Provenance = Literal["synthetic", "dev-baseline-2018", "battledim-2018-oof"]
FINAL_PROVENANCE: Provenance = "battledim-2018-oof"  # the only one that is not a mock (ADR-008 §B)

MAD_TO_SIGMA = 1.4826  # 1 / Phi^-1(3/4): the MAD of a normal series times this is its sigma

PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ForecasterIdentity(BaseModel):
    """``ModelInfo.name`` and ``version`` of the forecaster whose residuals were calibrated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)


class CalibrationArtefact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provenance: Provenance
    forecaster: ForecasterIdentity
    residuals_sha256: Sha256Hex = Field(description="sha256 of the residual file used.")
    folds_sha256: Sha256Hex = Field(description="sha256 of the fold definition used.")
    sigma_m: dict[SensorId, PositiveFiniteFloat] = Field(
        min_length=1, description="Residual sigma per pressure sensor, in metres (file order)."
    )
    k: PositiveFiniteFloat
    h: PositiveFiniteFloat
    n_min: int = Field(ge=1, strict=True, description="N: sensors above h that open an alert.")
    c: FiniteFloat = Field(
        gt=1, description="S is capped at c*h; bounds the recovery after a repair."
    )
    h_close: PositiveFiniteFloat = Field(
        description="A claimed sensor is released when S < h_close."
    )

    @property
    def is_mock(self) -> bool:
        """Every provenance but the final one forces ``ModelInfo.is_mock`` (ADR-008 §B)."""
        return self.provenance != FINAL_PROVENANCE

    @model_validator(mode="after")
    def _consistent(self) -> "CalibrationArtefact":
        if self.n_min > len(self.sigma_m):
            raise ValueError(f"n_min ({self.n_min}) exceeds the {len(self.sigma_m)} sensors")
        if self.h_close > self.h:
            raise ValueError(f"h_close ({self.h_close}) exceeds h ({self.h})")
        return self


def canonical_bytes(artefact: CalibrationArtefact) -> bytes:
    """The artefact file as written by ``write_artefact``: fields in declaration order, sensors
    in calibration order, two-space indent, one final newline. The artefact hash is the sha256
    of these bytes, so what is written and what is hashed in memory cannot differ."""
    return (artefact.model_dump_json(indent=2) + "\n").encode("utf-8")


def write_artefact(artefact: CalibrationArtefact, path: Path) -> None:
    path.write_bytes(canonical_bytes(artefact))


def calibrate(
    residuals: Mapping[str, Sequence[float]],
    *,
    provenance: Provenance,
    forecaster: ForecasterIdentity,
    residuals_sha256: str,
    folds_sha256: str,
    sigma_floor_m: float,
    k: float,
    h: float,
    n_min: int,
    c: float,
    h_close: float,
) -> CalibrationArtefact:
    """Estimate sigma per sensor and wrap it with the parameters chosen elsewhere (ADR-008 §C).

    ``residuals``: out-of-fold residuals in metres (observed - expected) per pressure sensor;
    the sensor order of the artefact is the order of this mapping. Sigma is
    ``max(sigma_floor_m, 1.4826 * MAD)`` per sensor, the median absolute deviation from the
    median, so a leak inside the series barely moves it. ``k``, ``h``, ``n_min``, ``c`` and
    ``h_close`` come from the pre-registered sweep, the hashes from the files the residuals and
    folds were read from: nothing here has a default (ADR-008 §B). Raises ``ValueError`` for no
    sensors, an empty or non-finite series, or a floor that is not a positive finite number.
    """
    if not (math.isfinite(sigma_floor_m) and sigma_floor_m > 0):
        raise ValueError(f"sigma_floor_m must be positive and finite, got {sigma_floor_m}")
    if not residuals:
        raise ValueError("no residual series to calibrate")
    sigma: dict[str, float] = {}
    for sensor, series in residuals.items():
        if len(series) == 0:
            raise ValueError(f"empty residual series for sensor {sensor!r}")
        if not all(math.isfinite(r) for r in series):
            raise ValueError(f"non-finite residual for sensor {sensor!r}")
        centre = statistics.median(series)
        mad = statistics.median(abs(r - centre) for r in series)
        sigma[sensor] = max(sigma_floor_m, MAD_TO_SIGMA * mad)
    return CalibrationArtefact(
        provenance=provenance,
        forecaster=forecaster,
        residuals_sha256=residuals_sha256,
        folds_sha256=folds_sha256,
        sigma_m=sigma,
        k=k,
        h=h,
        n_min=n_min,
        c=c,
        h_close=h_close,
    )
