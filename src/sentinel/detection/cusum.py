"""CUSUM detector (ADR-007), slices BE-012a/b/c/d.

Per-sensor ``S`` with cap ``c*h``, alert episodes per sensor (claim, release below
``h_close``, no reset), onset, decay across missing windows and all-or-nothing rejection of
bad input. ``ModelInfo`` carries the artefact hash and ``is_mock`` follows the calibration
provenance. BE-012e adds the region ranking (``regions.py``).
"""

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sentinel.config import ConfigError
from sentinel.detection.calibration import CalibrationArtefact, canonical_bytes
from sentinel.detection.interface import DetectionError
from sentinel.detection.regions import RegionMapping
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.detections import (
    AlertCandidate,
    CusumEvidence,
    DetectionResult,
    DeviatingSensor,
    SensorResidual,
)

# Semver of the detector code; ``ModelInfo.version`` appends the artefact hash (ADR-008 §B).
DETECTOR_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class _State:
    cusum: tuple[float, ...]  # S per sensor, calibration order
    claimed: frozenset[int]  # sensors in an open episode, until S < h_close
    last_zero: tuple[datetime | None, ...]  # last window with S = 0 per sensor (onset)
    last_timestamp: datetime | None  # last window evaluated


class CusumDetector:
    """Per-sensor one-sided CUSUM on ``z``, capped at ``c*h``; one alert when ``n_min``
    unclaimed sensors exceed ``h``. Every number comes from the calibration artefact.

    ``is_mock`` is true unless the calibration provenance is ``battledim-2018-oof`` (ADR-008 §B).

    ``evaluate`` validates first (timestamp after the last evaluated, sensor sets equal to the
    calibration, finite values) and raises ``DetectionError``; the state changes only in its
    last statement, so any exception leaves it untouched (ADR-007 §9).
    """

    def __init__(
        self,
        calibration: CalibrationArtefact,
        *,
        interval: timedelta,
        calibration_id: str | None = None,
        regions: RegionMapping | None = None,
    ) -> None:
        """``interval``: the window length (contract §1), used to count missing windows.
        ``calibration_id``: sha256 of the artefact file (``from_file`` sets it); by default
        the sha256 of the file ``write_artefact`` would write for ``calibration``.
        ``regions``: sensor -> region mapping (ADR-007 §8); it must cover exactly the
        calibration sensors (``ValueError`` otherwise). Without it ``region`` and
        ``region_scores`` stay null and the output is unchanged."""
        if interval <= timedelta(0):
            raise ValueError(f"interval must be positive, got {interval}")
        self._cal = calibration
        self._interval = interval
        self._sensors = tuple(calibration.sigma_m)
        self._sensor_set = frozenset(self._sensors)
        if regions is not None and regions.sensors != self._sensor_set:
            raise ValueError(
                "regions must cover exactly the calibration sensors: "
                f"missing={sorted(self._sensor_set - regions.sensors)} "
                f"unexpected={sorted(regions.sensors - self._sensor_set)}"
            )
        self._regions_id = None if regions is None else regions.regions_id
        index = {s: i for i, s in enumerate(self._sensors)}
        # Per region, its sensors' positions in calibration order: the order of every sum.
        self._regions: tuple[tuple[str, tuple[int, ...]], ...] = (
            ()
            if regions is None
            else tuple(
                (r, tuple(sorted(index[s] for s in ss))) for r, ss in regions.regions.items()
            )
        )
        self._state = _State(
            cusum=tuple(0.0 for _ in self._sensors),
            claimed=frozenset(),
            last_zero=tuple(None for _ in self._sensors),
            last_timestamp=None,
        )
        self._calibration_id = (
            calibration_id or hashlib.sha256(canonical_bytes(calibration)).hexdigest()
        )
        self._info = ModelInfo(
            name="cusum",
            version=f"{DETECTOR_VERSION}+{self._calibration_id[:8]}",
            is_mock=calibration.is_mock,
        )

    @classmethod
    def from_file(cls, path: Path, *, interval: timedelta) -> "CusumDetector":
        raw = path.read_bytes()
        artefact = CalibrationArtefact.model_validate_json(raw)
        return cls(artefact, interval=interval, calibration_id=hashlib.sha256(raw).hexdigest())

    @classmethod
    def for_pipeline(
        cls,
        path: Path,
        *,
        interval: timedelta,
        pressure_sensors: Sequence[str],
        forecaster: ModelInfo,
        regions: Path | None = None,
    ) -> "CusumDetector":
        """The detector as the server starts it: ``from_file`` plus the startup checks. With
        ``regions`` (a mapping file), the mapping must cover exactly ``pressure_sensors``."""
        detector = cls.from_file(path, interval=interval)
        registry = frozenset(pressure_sensors)
        if detector._sensor_set != registry:
            raise ConfigError(
                "calibration sensors differ from the registry's pressure sensors: "
                f"missing={sorted(registry - detector._sensor_set)} "
                f"unexpected={sorted(detector._sensor_set - registry)}"
            )
        order = tuple(pressure_sensors)
        if detector._sensors != order:
            at = next(
                i for i, (a, b) in enumerate(zip(detector._sensors, order, strict=True)) if a != b
            )
            raise ConfigError(
                "calibration lists the pressure sensors in a different order from the "
                f"registry (first difference at position {at}: calibration "
                f"{detector._sensors[at]!r}, registry {order[at]!r}); the order must be equal "
                "(ADR-007 §9)"
            )
        calibrated = detector._cal.forecaster
        if (forecaster.name, forecaster.version) != (calibrated.name, calibrated.version):
            raise ConfigError(
                f"calibration was made for forecaster {calibrated.name} {calibrated.version}, "
                f"but the pipeline serves {forecaster.name} {forecaster.version}"
            )
        if regions is None:
            return detector
        mapping = RegionMapping.from_file(regions)
        if mapping.sensors != registry:
            raise ConfigError(
                "regions differ from the registry's pressure sensors: "
                f"missing={sorted(registry - mapping.sensors)} "
                f"unexpected={sorted(mapping.sensors - registry)}"
            )
        return cls(
            detector._cal,
            interval=interval,
            calibration_id=detector._calibration_id,
            regions=mapping,
        )

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
        # z = (expected - observed) / sigma: positive on a pressure drop (Q-DET-02, contract §3).
        z = tuple(-r / cal.sigma_m[s] for r, s in zip(residual, self._sensors, strict=True))
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
        # A pressure drop (z > 0) is positive evidence (design doc §3).
        cusum = tuple(
            min(cap, max(0.0, s_prev + zi - cal.k)) for s_prev, zi in zip(decayed, z, strict=True)
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
        region_scores = self._region_scores(cusum, above) if self._regions else None
        alerts: tuple[AlertCandidate, ...] = ()
        if statistic is not None and statistic > cal.h:
            claimed = claimed | frozenset(above)
            # Earliest since; unknown (None) if a sensor's S was never 0, because its onset
            # precedes the first window this detector evaluated.
            onsets = [last_zero[i] for i in above]
            alerts = (
                AlertCandidate(
                    # The alert's sensors are exactly the unclaimed ones above h, so the sums
                    # over them are region_scores (ADR-007 §8).
                    region=None if region_scores is None else _top_region(region_scores),
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
                    summary=f"{'[MOCK] ' if cal.is_mock else ''}CUSUM above h on "
                    f"{len(above)} sensors ({cal.provenance} calibration)",
                ),
            )
        diagnostics: dict[str, float | int | str | bool] = {
            "k": cal.k,
            "h": cal.h,
            "n_min": cal.n_min,
            "c": cal.c,
            "cap": cap,  # c*h: the bound on S
            "h_close": cal.h_close,
            "calibration_id": self._calibration_id,
            "provenance": cal.provenance,
            "skipped_windows": skipped,
        }
        if self._regions_id is not None:
            diagnostics["regions_id"] = self._regions_id
        if emptied:
            diagnostics["reset_reason"] = "gap"  # the skip emptied every S (ADR-007 §7)
        result = DetectionResult(
            timestamp=timestamp,
            model=self._info,
            state="alarm" if claimed else ("suspect" if above else "normal"),
            anomaly_score=statistic,
            residuals=residuals,
            alerts=alerts,
            region_scores=region_scores,
            diagnostics=diagnostics,
        )
        new = _State(cusum=cusum, claimed=claimed, last_zero=last_zero, last_timestamp=timestamp)
        # Everything above is pure; the state changes in one assignment (ADR-007 §9).
        self._state = new
        return result

    def _region_scores(self, cusum: Sequence[float], above: Sequence[int]) -> dict[str, float]:
        """Per region, the sum of ``S`` over the unclaimed sensors above ``h`` (ADR-007 §8);
        ``above`` is that set. Plain float64 additions in calibration order (ADR-007 §9)."""
        scores: dict[str, float] = {}
        unclaimed_above = frozenset(above)
        for region, members in self._regions:
            total = 0.0
            for i in members:
                if i in unclaimed_above:
                    total += cusum[i]
            scores[region] = total
        return scores

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


def _top_region(scores: Mapping[str, float]) -> str:
    """The region with the largest score; ties go to the first in the file (``max`` keeps the
    first of equal values)."""
    return max(scores, key=lambda region: scores[region])
