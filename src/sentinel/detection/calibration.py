"""Calibration artefact of the CUSUM detector (ADR-008 §B).

Every number the detector uses comes from this file; none has a default in code. Fields so
far: sigma per sensor, ``k``, ``h``, ``n_min`` (N), ``c`` (cap ``c*h``), ``h_close`` and
``provenance``.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sentinel.schemas.common import FiniteFloat
from sentinel.schemas.windows import SensorId

Provenance = Literal["synthetic", "dev-baseline-2018", "battledim-2018-oof"]

PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]


class CalibrationArtefact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provenance: Provenance
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

    @model_validator(mode="after")
    def _consistent(self) -> "CalibrationArtefact":
        if self.n_min > len(self.sigma_m):
            raise ValueError(f"n_min ({self.n_min}) exceeds the {len(self.sigma_m)} sensors")
        if self.h_close > self.h:
            raise ValueError(f"h_close ({self.h_close}) exceeds h ({self.h})")
        return self
