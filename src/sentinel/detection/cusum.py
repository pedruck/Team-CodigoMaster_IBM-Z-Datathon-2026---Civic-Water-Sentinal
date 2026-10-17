"""CUSUM detector (ADR-007), tracer-bullet slice BE-012a.

Simplified on purpose: per-sensor ``S`` without cap, a claimed sensor is never released, no
gap rule, no region.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sentinel.detection.calibration import CalibrationArtefact
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.detections import (
    AlertCandidate,
    CusumEvidence,
    DetectionResult,
    DeviatingSensor,
    SensorResidual,
)


@dataclass(frozen=True, slots=True)
class _State:
    cusum: tuple[float, ...]  # S per sensor, calibration order
    claimed: frozenset[int]  # indices of sensors that already opened an alert


class CusumDetector:
    """Per-sensor one-sided CUSUM on ``d = -z``; one alert when ``n_min`` unclaimed sensors
    exceed ``h``. Every number comes from the calibration artefact.

    ``is_mock`` is always true in this slice: without the cap, the release of claimed sensors
    and the gap rule (BE-012b/c) it is not the detector of ADR-007, whatever the provenance.
    """

    def __init__(self, calibration: CalibrationArtefact) -> None:
        self._cal = calibration
        self._sensors = tuple(calibration.sigma_m)
        self._state = _State(cusum=tuple(0.0 for _ in self._sensors), claimed=frozenset())
        self._info = ModelInfo(name="cusum", version="0.1.0", is_mock=True)

    @classmethod
    def from_file(cls, path: Path) -> "CusumDetector":
        return cls(CalibrationArtefact.model_validate_json(path.read_text(encoding="utf-8")))

    @property
    def info(self) -> ModelInfo:
        return self._info

    def evaluate(
        self,
        observed: Mapping[str, float],
        expected: Mapping[str, float],
        timestamp: datetime,
    ) -> DetectionResult:
        cal, state = self._cal, self._state
        residual = tuple(observed[s] - expected[s] for s in self._sensors)
        z = tuple(r / cal.sigma_m[s] for r, s in zip(residual, self._sensors, strict=True))
        # d = -z: a pressure drop is positive evidence (design doc §3 sign).
        cusum = tuple(
            max(0.0, s_prev - zi - cal.k) for s_prev, zi in zip(state.cusum, z, strict=True)
        )
        residuals = {
            s: SensorResidual(residual_m=residual[i], z=z[i], cusum=cusum[i])
            for i, s in enumerate(self._sensors)
        }

        # Claimed sensors never count again (BE-012a: never released). Deterministic order:
        # largest S first, ties by artefact order (ADR-007 §9 asks for registry order: the
        # startup check of BE-012d must require both orders to be equal).
        unclaimed = (i for i in range(len(self._sensors)) if i not in state.claimed)
        ranked = sorted(unclaimed, key=lambda i: (-cusum[i], i))
        above = [i for i in ranked if cusum[i] > cal.h]
        # N-th largest unclaimed S: an alert opens iff it exceeds h (ADR-007 §3). None once
        # fewer than N sensors are unclaimed (claimed sensors are never released here).
        statistic = cusum[ranked[cal.n_min - 1]] if len(ranked) >= cal.n_min else None
        claimed = state.claimed
        alerts: tuple[AlertCandidate, ...] = ()
        if statistic is not None and statistic > cal.h:
            claimed = claimed | frozenset(above)
            alerts = (
                AlertCandidate(
                    cusum=CusumEvidence(statistic=statistic, threshold=cal.h),
                    deviating_sensors=tuple(
                        DeviatingSensor(sensor=self._sensors[i], deviation_m=residual[i], z=z[i])
                        for i in above
                    ),
                    summary=f"[MOCK] CUSUM above h on {len(above)} sensors "
                    f"(simplified detector, {cal.provenance} calibration)",
                ),
            )
        result = DetectionResult(
            timestamp=timestamp,
            model=self._info,
            state="alarm" if claimed else ("suspect" if above else "normal"),
            anomaly_score=statistic,
            residuals=residuals,
            alerts=alerts,
            diagnostics={"k": cal.k, "h": cal.h, "n_min": cal.n_min, "provenance": cal.provenance},
        )
        new = _State(cusum=cusum, claimed=claimed)
        # Everything above is pure; the state changes in one assignment (ADR-007 §9).
        self._state = new
        return result
