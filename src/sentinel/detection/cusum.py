"""CUSUM detector (ADR-007), slices BE-012a/b/c.

Per-sensor ``S`` with cap ``c*h``, alert episodes per sensor (claim, release below
``h_close``, no reset), onset, decay across missing windows and all-or-nothing rejection of
bad input. Not yet: startup checks and provenance rule (BE-012d), region (BE-012e).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sentinel.detection.calibration import CalibrationArtefact
from sentinel.detection.interface import DetectionError
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
    last_timestamp: datetime | None  # last window evaluated


class CusumDetector:
    """Per-sensor one-sided CUSUM on ``d = -z``, capped at ``c*h``; one alert when ``n_min``
    unclaimed sensors exceed ``h``. Every number comes from the calibration artefact.

    ``is_mock`` is always true for now: without the startup checks of BE-012d it is not the
    detector of ADR-007, whatever the provenance.

    ``evaluate`` validates first (timestamp after the last evaluated, sensor sets equal to the
    calibration, finite values) and raises ``DetectionError``; the state changes only in its
    last statement, so any exception leaves it untouched (ADR-007 §9).
    """

    def __init__(self, calibration: CalibrationArtefact, *, interval: timedelta) -> None:
        """``interval``: the window length (contract §1), used to count missing windows."""
        if interval <= timedelta(0):
            raise ValueError(f"interval must be positive, got {interval}")
        self._cal = calibration
        self._interval = interval
        self._sensors = tuple(calibration.sigma_m)
        self._sensor_set = frozenset(self._sensors)
        self._state = _State(
            cusum=tuple(0.0 for _ in self._sensors),
            claimed=frozenset(),
            last_zero=tuple(None for _ in self._sensors),
            last_timestamp=None,
        )
        self._info = ModelInfo(name="cusum", version="0.1.0", is_mock=True)

    @classmethod
    def from_file(cls, path: Path, *, interval: timedelta) -> "CusumDetector":
        artefact = CalibrationArtefact.model_validate_json(path.read_text(encoding="utf-8"))
        return cls(artefact, interval=interval)

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
        self._validate(observed, expected, timestamp)
        residual = tuple(observed[s] - expected[s] for s in self._sensors)
        z = tuple(r / cal.sigma_m[s] for r, s in zip(residual, self._sensors, strict=True))
        overflow = [
            s
            for s, r, zi in zip(self._sensors, residual, z, strict=True)
            if not (math.isfinite(r) and math.isfinite(zi))
        ]
        if overflow:  # a tiny sigma or huge readings: z must stay finite (ADR-007 §9)
            raise DetectionError(f"non-finite residual or z for sensors {overflow}")
        # Each missing window counts as a zero residual: S decays by k per window (ADR-007 §7).
        skipped = (
            0
            if state.last_timestamp is None
            else max(0, (timestamp - state.last_timestamp) // self._interval - 1)
        )
        decayed = tuple(max(0.0, s - cal.k * skipped) for s in state.cusum)
        emptied = any(state.cusum) and not any(decayed)
        cap = cal.c * cal.h
        # d = -z: a pressure drop is positive evidence (design doc §3 sign).
        cusum = tuple(
            min(cap, max(0.0, s_prev - zi - cal.k)) for s_prev, zi in zip(decayed, z, strict=True)
        )
        # Onset: the last window with S = 0, counting the missing windows, where S is 0 at the
        # end of the gap whenever the decay left it at 0.
        gap_end = timestamp - self._interval if skipped else None
        last_zero = tuple(
            timestamp
            if s_now == 0.0
            else (gap_end if gap_end is not None and s_gap == 0.0 else since)
            for s_now, s_gap, since in zip(cusum, decayed, state.last_zero, strict=True)
        )
        residuals = {
            s: SensorResidual(residual_m=residual[i], z=z[i], cusum=cusum[i])
            for i, s in enumerate(self._sensors)
        }

        # A claimed sensor counts again only once S < h_close (ADR-007 §4). Deterministic
        # order: largest S first, ties by artefact order (ADR-007 §9 asks for registry order:
        # the startup check of BE-012d must require both orders to be equal).
        # The decayed S is the lowest S of the gap: below h_close the sensor was released there.
        claimed = frozenset(i for i in state.claimed if min(decayed[i], cusum[i]) >= cal.h_close)
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
        diagnostics: dict[str, float | int | str | bool] = {
            "k": cal.k,
            "h": cal.h,
            "n_min": cal.n_min,
            "c": cal.c,
            "cap": cap,  # c*h: the bound on S
            "h_close": cal.h_close,
            "provenance": cal.provenance,
            "skipped_windows": skipped,
        }
        if emptied:
            diagnostics["reset_reason"] = "gap"  # the skip emptied every S (ADR-007 §7)
        result = DetectionResult(
            timestamp=timestamp,
            model=self._info,
            state="alarm" if claimed else ("suspect" if above else "normal"),
            anomaly_score=statistic,
            residuals=residuals,
            alerts=alerts,
            diagnostics=diagnostics,
        )
        new = _State(cusum=cusum, claimed=claimed, last_zero=last_zero, last_timestamp=timestamp)
        # Everything above is pure; the state changes in one assignment (ADR-007 §9).
        self._state = new
        return result

    def _validate(
        self, observed: Mapping[str, float], expected: Mapping[str, float], timestamp: datetime
    ) -> None:
        """All-or-nothing input checks (ADR-007 §9); raises before anything is computed."""
        if timestamp.utcoffset() is None:
            raise DetectionError(f"window timestamp {timestamp.isoformat()} has no timezone")
        last = self._state.last_timestamp
        if last is not None and timestamp <= last:
            raise DetectionError(
                f"window {timestamp.isoformat()} is not after the last evaluated {last.isoformat()}"
            )
        for name, values in (("observed", observed), ("expected", expected)):
            if values.keys() != self._sensor_set:
                missing = sorted(self._sensor_set - values.keys())
                unexpected = sorted(values.keys() - self._sensor_set)
                raise DetectionError(
                    f"{name} sensors differ from the calibration: "
                    f"missing={missing} unexpected={unexpected}"
                )
            bad = sorted(s for s, v in values.items() if not math.isfinite(v))
            if bad:
                raise DetectionError(f"non-finite {name} value for sensors {bad}")


def _earliest(onsets: list[datetime | None]) -> datetime | None:
    known = [t for t in onsets if t is not None]
    return min(known, default=None) if len(known) == len(onsets) else None
