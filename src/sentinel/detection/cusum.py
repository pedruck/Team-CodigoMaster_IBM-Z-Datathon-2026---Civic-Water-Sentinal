"""CUSUM detector (ADR-007), slices BE-012a/b.

Per-sensor ``S`` with cap ``c*h``, alert episodes per sensor (claim, release below
``h_close``, no reset) and onset. Not yet: gap rule and input rejection (BE-012c), startup
checks and provenance rule (BE-012d), region (BE-012e).
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
    claimed: frozenset[int]  # sensors in an open episode, until S < h_close
    last_zero: tuple[datetime | None, ...]  # last window with S = 0 per sensor (onset)


class CusumDetector:
    """Per-sensor one-sided CUSUM on ``d = -z``, capped at ``c*h``; one alert when ``n_min``
    unclaimed sensors exceed ``h``. Every number comes from the calibration artefact.

    ``is_mock`` is always true for now: without the gap rule (BE-012c) and the startup checks
    (BE-012d) it is not the detector of ADR-007, whatever the provenance.
    """

    def __init__(self, calibration: CalibrationArtefact) -> None:
        self._cal = calibration
        self._sensors = tuple(calibration.sigma_m)
        self._state = _State(
            cusum=tuple(0.0 for _ in self._sensors),
            claimed=frozenset(),
            last_zero=tuple(None for _ in self._sensors),
        )
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
        cap = cal.c * cal.h
        cusum = tuple(
            min(cap, max(0.0, s_prev - zi - cal.k))
            for s_prev, zi in zip(state.cusum, z, strict=True)
        )
        last_zero = tuple(
            timestamp if s_now == 0.0 else since
            for s_now, since in zip(cusum, state.last_zero, strict=True)
        )
        residuals = {
            s: SensorResidual(residual_m=residual[i], z=z[i], cusum=cusum[i])
            for i, s in enumerate(self._sensors)
        }

        # A claimed sensor counts again only once S < h_close (ADR-007 §4). Deterministic
        # order: largest S first, ties by artefact order (ADR-007 §9 asks for registry order:
        # the startup check of BE-012d must require both orders to be equal).
        claimed = frozenset(i for i in state.claimed if cusum[i] >= cal.h_close)
        unclaimed = (i for i in range(len(self._sensors)) if i not in claimed)
        ranked = sorted(unclaimed, key=lambda i: (-cusum[i], i))
        above = [i for i in ranked if cusum[i] > cal.h]
        # N-th largest unclaimed S: an alert opens iff it exceeds h (ADR-007 §3). None while
        # fewer than N sensors are unclaimed.
        statistic = cusum[ranked[cal.n_min - 1]] if len(ranked) >= cal.n_min else None
        alerts: tuple[AlertCandidate, ...] = ()
        if statistic is not None and statistic > cal.h:
            claimed = claimed | frozenset(above)
            # Earliest since; unknown (None) if a sensor's S was never 0, because its onset
            # precedes the first window this detector evaluated.
            onsets = [last_zero[i] for i in above]
            alerts = (
                AlertCandidate(
                    suspected_onset=_earliest(onsets),
                    cusum=CusumEvidence(statistic=statistic, threshold=cal.h),
                    deviating_sensors=tuple(
                        DeviatingSensor(
                            sensor=self._sensors[i],
                            deviation_m=residual[i],
                            z=z[i],
                            since=last_zero[i],
                        )
                        for i in above
                    ),
                    summary=f"[MOCK] CUSUM above h on {len(above)} sensors "
                    f"(incomplete detector, {cal.provenance} calibration)",
                ),
            )
        result = DetectionResult(
            timestamp=timestamp,
            model=self._info,
            state="alarm" if claimed else ("suspect" if above else "normal"),
            anomaly_score=statistic,
            residuals=residuals,
            alerts=alerts,
            diagnostics={
                "k": cal.k,
                "h": cal.h,
                "n_min": cal.n_min,
                "c": cal.c,
                "cap": cap,  # c*h: the bound on S
                "h_close": cal.h_close,
                "provenance": cal.provenance,
            },
        )
        new = _State(cusum=cusum, claimed=claimed, last_zero=last_zero)
        # Everything above is pure; the state changes in one assignment (ADR-007 §9).
        self._state = new
        return result


def _earliest(onsets: list[datetime | None]) -> datetime | None:
    known = [t for t in onsets if t is not None]
    return min(known, default=None) if len(known) == len(onsets) else None
