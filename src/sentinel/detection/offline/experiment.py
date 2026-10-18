"""Calibration experiment of the CUSUM detector on the ML owner's 2018 out-of-fold residuals
(BE-012j; it runs BE-012h; ADR-008 §C, §D, §G).

Two phases with a registration commit between them (pre-registration, ADR-008 §G):

1. ``explore``: sigma per sensor (also with the leak windows masked), the measurement resolution
   and the sigma floor it implies, the lag-1 autocorrelation, and the displacement and
   persistence of ``z`` at the abrupt leaks of 2018, which give ``delta_N`` and
   ``k = delta_N / 2``. Nothing is replayed: no detection result exists after this phase.
2. The detection owner copies ``k_by_n`` and fixes the grids, the budgets and the recovery
   target in the ``sweep`` section of the protocol file, and commits it.
3. ``sweep``: the ``(h, N)`` grid with the registered ``k``, the Pareto front, the operating
   point of every candidate budget with leave-one-leak-out, the cap and the release threshold
   chosen on the recovery after the 2018 repairs, the ``k`` sensitivity, the false alarms per
   sensor for several sigma floors, and the candidate artefact.

Usage (``--data-dir`` defaults to ``$SENTINEL_DATA_DIR``)::

    python -m sentinel.detection.offline.experiment explore \\
        --protocol config/calibration-protocol.xgboost-33.json --variant with-neighbours \\
        --residuals-dir $SENTINEL_DATA_DIR/v1/oof/xgboost-33@0.1.0/with-neighbours \\
        --out-dir $SENTINEL_DATA_DIR/p4_detect/xgboost-33-oof/with-neighbours
    python -m sentinel.detection.offline.experiment sweep ... --provenance battledim-2018-oof

Every number comes from the protocol file (pydantic, no defaults) or from the data, and the
outputs carry the sha256 of the protocol and of every input. Offline only (numpy, pandas); the
server never imports it; no 2019 file is opened (ADR-008 §A).
"""

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
import numpy.typing as npt
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from sentinel.data.clean_mask import mascara_limpa
from sentinel.detection.calibration import (
    CalibrationArtefact,
    ForecasterIdentity,
    Provenance,
    Sha256Hex,
    calibrate,
    canonical_bytes,
    robust_sigma,
    write_artefact,
)
from sentinel.detection.offline.layer1 import LEAK_TYPES, Layer1Result, MatchRule, evaluate
from sentinel.detection.offline.oof import (
    OofData,
    inflation_factor,
    lag1_autocorrelation,
    load_oof_residuals,
    quantization_step,
    refuse_2019,
    z_matrix,
)
from sentinel.detection.offline.persistence import (
    Delta,
    LeakDisplacement,
    PersistenceVerdict,
    delta_by_rank,
    leak_displacement,
    load_leaks_2018,
    persistence_verdict,
)
from sentinel.detection.offline.selection import (
    Cell,
    LoloSummary,
    Recovery,
    cell_table,
    evaluate_cells,
    leave_one_leak_out,
    pareto_front,
    recovery_times,
    replay_cells,
    select,
)
from sentinel.detection.offline.sweep import Opening, trajectory
from sentinel.schemas.common import FiniteFloat
from sentinel.sensors import SensorRegistry

PROTOCOL_ID = "cusum-calibration/1"
NOT_REGISTERED = "not yet registered"
MaskPolicy = Literal["none", "reparados", "reparados+crescimento", "todos"]
MASK_POLICIES: tuple[MaskPolicy, ...] = ("none", "reparados", "reparados+crescimento", "todos")
FLOOR_RULE = "quantization step / sqrt(12) of the coarsest sensor"
FILE_DECIMALS = 6  # the ML owner writes the pressures with %.6f (1 micrometre)
WINDOW = timedelta(minutes=5)
EXPLORE_JSON = "explore.json"
EXPLORE_MD = "explore.md"
REPORT_MD = "report.md"
NOTICE = (
    "simulated benchmark (BattLeDIM L-Town); parameters fixed on 2018 out-of-fold residuals; "
    "no 2019 file opened; matching rule = the operator view of ADR-010 (7 days), provisional "
    "until Q-UI-06 is registered; counts n/N only, no confidence intervals (ADR-008 §F)"
)

Positive = Annotated[FiniteFloat, Field(gt=0)]
Fraction = Annotated[FiniteFloat, Field(gt=0, le=1)]
AboveOne = Annotated[FiniteFloat, Field(gt=1)]
NKey = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]
Text = Annotated[str, StringConstraints(min_length=1)]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SigmaRule(_Frozen):
    """sigma = max(floor, 1.4826 MAD of the residuals of the windows the mask keeps)."""

    mask_policy: MaskPolicy
    floor: Literal["quantization step / sqrt(12) of the coarsest sensor"]
    rationale: Text


class PersistenceRule(_Frozen):
    """The spans of ``persistence.leak_displacement`` and the acceptance of ADR-008 §G."""

    baseline_hours: Positive
    initial_hours: Positive
    remaining_after_hours: Positive
    n_top: int = Field(ge=1, strict=True)
    median_at_least: Fraction
    minimum_at_least: Fraction
    rationale: Text


class DeltaRule(_Frozen):
    """``delta_N`` over ``sustained_hours`` for N = 1..max_rank; ``k_N = round(delta_N / 2,
    k_decimals)``; an ``N`` whose ``k_N`` is not positive leaves the grid."""

    sustained_hours: Positive
    max_rank: int = Field(ge=1, strict=True)
    k_decimals: int = Field(ge=0, le=6, strict=True)
    rationale: Text


class Exploration(_Frozen):
    registered: date
    sigma: SigmaRule
    persistence: PersistenceRule
    delta: DeltaRule


class VariantInput(_Frozen):
    """The residual files a variant is calibrated on, pinned by hash before they are read."""

    forecaster: ForecasterIdentity
    residuals_sha256: Sha256Hex
    folds_sha256: Sha256Hex


class SweepRules(_Frozen):
    """Registered after ``explore`` and before ``sweep`` (ADR-008 §G)."""

    registered: date
    explore_sha256: dict[str, Sha256Hex] = Field(min_length=1)
    k_by_n: dict[str, dict[NKey, Positive]] = Field(min_length=1)
    k_sensitivity: list[Positive] = Field(min_length=1)
    h_values: list[Positive] = Field(min_length=1)
    base_c: AboveOne
    base_h_close_ratio: Fraction
    c_values: list[AboveOne] = Field(min_length=1)
    h_close_ratios: list[Fraction] = Field(min_length=1)
    max_delay_hours: dict[Literal["abrupt", "incipient"], Positive]
    budgets_per_week: list[Positive] = Field(min_length=1)
    primary_budget_per_week: Positive
    recovery_target_hours: Positive
    extra_floors_m: list[Positive]
    rationale: Text

    @model_validator(mode="after")
    def _consistent(self) -> "SweepRules":
        if self.h_values != sorted(set(self.h_values)):
            raise ValueError("h_values must be strictly ascending")
        lists: tuple[tuple[str, list[float]], ...] = (
            ("k_sensitivity", self.k_sensitivity),
            ("c_values", self.c_values),
            ("h_close_ratios", self.h_close_ratios),
            ("budgets_per_week", self.budgets_per_week),
        )
        for name, values in lists:
            if len(set(values)) != len(values):
                raise ValueError(f"{name} has repeated values")
        if self.base_c not in self.c_values:
            raise ValueError("base_c must be one of c_values")
        if self.base_h_close_ratio not in self.h_close_ratios:
            raise ValueError("base_h_close_ratio must be one of h_close_ratios")
        if self.primary_budget_per_week not in self.budgets_per_week:
            raise ValueError("primary_budget_per_week must be one of budgets_per_week")
        if set(self.max_delay_hours) != set(LEAK_TYPES):
            raise ValueError(f"max_delay_hours needs exactly the leak types {LEAK_TYPES}")
        if 1.0 in self.k_sensitivity:
            raise ValueError("k_sensitivity lists the other multipliers; 1 is the main grid")
        return self


class Protocol(_Frozen):
    protocol: Literal["cusum-calibration/1"]
    exploration: Exploration
    inputs: dict[str, VariantInput] = Field(min_length=1)
    sweep: SweepRules | Literal["not yet registered"]


def load_protocol(path: Path) -> tuple[Protocol, str]:
    """The validated protocol and the sha256 of the file."""
    raw = path.read_bytes()
    return Protocol.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def _canonical(document: Any) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def exploration_sha256(protocol: Protocol) -> str:
    """Hash of the exploration rules alone: registering the sweep does not change it."""
    return hashlib.sha256(_canonical(protocol.exploration.model_dump(mode="json"))).hexdigest()


def check_inputs(protocol: Protocol, variant: str, oof: OofData) -> VariantInput:
    """The variant is in the protocol and its files are the pinned ones (``ValueError``)."""
    pinned = protocol.inputs.get(variant)
    if pinned is None:
        raise ValueError(
            f"variant {variant!r} is not in the protocol inputs {list(protocol.inputs)}"
        )
    found = {
        "forecaster": oof.forecaster,
        "residuals_sha256": oof.residuals_sha256,
        "folds_sha256": oof.folds_sha256,
    }
    for name, value in found.items():
        if getattr(pinned, name) != value:
            raise ValueError(
                f"{name} of the files is {value}, the protocol pins {getattr(pinned, name)}"
            )
    return pinned


def clean_windows(
    index: pd.DatetimeIndex, leaks: pd.DataFrame, policy: MaskPolicy
) -> npt.NDArray[np.bool_]:
    """Windows kept by a mask policy of ``sentinel.data.clean_mask`` (``none``: all)."""
    if policy == "none":
        return np.ones(len(index), dtype=np.bool_)
    return mascara_limpa(index, leaks, policy).to_numpy(dtype=np.bool_)


# --- Phase A ---------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class ExploreResult:
    """Everything phase A measures. ``sigma``: the calibration sigma (mask + floor), calibration
    order; ``z``: the ``z`` it gives; ``k_by_n``: ``N -> k`` for the ``N`` that stay in the
    grid; ``document``: the content of ``explore.json`` (deterministic, no run metadata)."""

    variant: str
    sigma: dict[str, float]
    mad_sigma: pd.DataFrame
    floor_m: float
    z: pd.DataFrame
    displacements: list[LeakDisplacement]
    deltas: list[Delta]
    k_by_n: dict[int, float]
    verdict: PersistenceVerdict
    document: dict[str, Any] = field(repr=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical(self.document)).hexdigest()


def explore(
    protocol: Protocol, variant: str, oof: OofData, leaks: pd.DataFrame, leaks_sha256: str
) -> ExploreResult:
    """Phase A on one variant (module docstring); pure, writes nothing."""
    check_inputs(protocol, variant, oof)
    rules = protocol.exploration
    index = pd.DatetimeIndex(oof.observed.index)
    residuals = oof.residuals
    sensors = [str(s) for s in residuals.columns]
    values = residuals.to_numpy(dtype=np.float64)
    masks = {policy: clean_windows(index, leaks, policy) for policy in MASK_POLICIES}
    mad_sigma = pd.DataFrame(
        {
            policy: [robust_sigma(values[keep, j].tolist()) for j in range(len(sensors))]
            for policy, keep in masks.items()
        },
        index=sensors,
    )
    step = quantization_step(oof.observed, decimals=FILE_DECIMALS)
    coarsest = float(step.max())
    floor_m = coarsest / math.sqrt(12.0)
    chosen = mad_sigma[rules.sigma.mask_policy]
    sigma = {s: max(floor_m, float(chosen[s])) for s in sensors}
    phi = lag1_autocorrelation(residuals)
    z = pd.DataFrame(z_matrix(residuals, sigma), index=index, columns=sensors)
    p = rules.persistence
    displacements = leak_displacement(
        z,
        leaks,
        n_top=p.n_top,
        max_rank=rules.delta.max_rank,
        baseline=timedelta(hours=p.baseline_hours),
        initial=timedelta(hours=p.initial_hours),
        remaining_after=timedelta(hours=p.remaining_after_hours),
        sustained=timedelta(hours=rules.delta.sustained_hours),
    )
    deltas = delta_by_rank(displacements)
    k_by_n = {
        d.rank: round(d.delta / 2, rules.delta.k_decimals)
        for d in deltas
        if round(d.delta / 2, rules.delta.k_decimals) > 0
    }
    verdict = persistence_verdict(
        displacements, median_at_least=p.median_at_least, minimum_at_least=p.minimum_at_least
    )
    inflation = {s: inflation_factor(float(phi[s])) for s in sensors}
    document = {
        "variant": variant,
        "forecaster": oof.forecaster.model_dump(),
        "residuals_sha256": oof.residuals_sha256,
        "folds_sha256": oof.folds_sha256,
        "leaks_2018_sha256": leaks_sha256,
        "exploration_rules_sha256": exploration_sha256(protocol),
        "windows": len(index),
        "clean_windows": {policy: int(keep.sum()) for policy, keep in masks.items()},
        "sigma": {
            "mask_policy": rules.sigma.mask_policy,
            "floor_rule": FLOOR_RULE,
            "coarsest_step_m": coarsest,
            "floor_m": floor_m,
            "sensors": [
                {
                    "sensor": s,
                    "mad_sigma_m": {
                        policy: float(mad_sigma[policy][s]) for policy in MASK_POLICIES
                    },
                    "sigma_m": sigma[s],
                    "floor_binds": float(chosen[s]) < floor_m,
                    "quantization_step_m": float(step[s]),
                    "lag1_autocorr": float(phi[s]),
                    "inflation": inflation[s],
                }
                for s in sensors
            ],
        },
        "persistence": {
            "leaks": [
                {
                    "pipe": d.pipe,
                    "start": d.start.isoformat(),
                    "end": d.end.isoformat(),
                    "top_sensors": list(d.top_sensors),
                    "initial": list(d.initial),
                    "remaining": list(d.remaining),
                    "persistence": d.persistence,
                    "sustained_sensors": list(d.sustained_sensors),
                    "sustained": list(d.sustained),
                }
                for d in displacements
            ],
            "median": verdict.median,
            "minimum": verdict.minimum,
            "passed": verdict.passed,
        },
        "delta": [
            {
                "n": d.rank,
                "delta": d.delta,
                "weakest_pipe": d.weakest_pipe,
                "k": k_by_n.get(d.rank),
            }
            for d in deltas
        ],
        "grid_guidance": {
            "lag1_median": float(phi.median()),
            "inflation_median": float(statistics.median(inflation.values())),
            "inflation_max": max(inflation.values()),
        },
    }
    return ExploreResult(
        variant=variant,
        sigma=sigma,
        mad_sigma=mad_sigma,
        floor_m=floor_m,
        z=z,
        displacements=displacements,
        deltas=deltas,
        k_by_n=k_by_n,
        verdict=verdict,
        document=document,
    )


def _num(value: float | None, digits: int = 4) -> str:
    if value is None or math.isnan(value):
        return "—"
    return f"{value:.{digits}g}"


def _markdown(table: pd.DataFrame) -> str:
    header = "| " + " | ".join(str(c) for c in table.columns) + " |"
    rule = "|" + "---|" * len(table.columns)
    body = [
        "| "
        + " | ".join(_num(v) if isinstance(v, float) or v is None else str(v) for v in row)
        + " |"
        for row in table.itertuples(index=False)
    ]
    return "\n".join([header, rule, *body])


def _commit() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            # Only what the run depends on: a documentation edit does not change a result.
            ["git", "status", "--porcelain", "--untracked-files=no", "--", "src", "config"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return head + (" (with uncommitted changes)" if dirty else "")


def explore_markdown(result: ExploreResult, protocol_sha256: str) -> str:
    doc = result.document
    sigma = doc["sigma"]
    sensors = pd.DataFrame(
        [
            {
                "sensor": row["sensor"],
                **{f"sigma MAD {p} (mm)": 1000 * row["mad_sigma_m"][p] for p in MASK_POLICIES},
                "sigma (mm)": 1000 * row["sigma_m"],
                "floor binds": "yes" if row["floor_binds"] else "",
                "Δ (mm)": 1000 * row["quantization_step_m"],
                "φ lag 1": row["lag1_autocorr"],
                "(1+φ)/(1-φ)": row["inflation"],
            }
            for row in sigma["sensors"]
        ]
    )
    leaks = pd.DataFrame(
        [
            {
                "pipe": d.pipe,
                "start": d.start.isoformat(sep=" "),
                "top sensors": " ".join(d.top_sensors),
                "initial z": " ".join(_num(v, 3) for v in d.initial),
                "remaining z": " ".join(_num(v, 3) for v in d.remaining),
                "persistence": d.persistence,
                "sustained z by rank": " ".join(_num(v, 3) for v in d.sustained),
            }
            for d in result.displacements
        ]
    )
    deltas = pd.DataFrame(
        [
            {
                "N": d.rank,
                "δ_N": d.delta,
                "weakest leak": d.weakest_pipe,
                "k_N": result.k_by_n.get(d.rank),
            }
            for d in result.deltas
        ]
    )
    v = result.verdict
    guidance = doc["grid_guidance"]
    return "\n".join(
        [
            f"# Calibration experiment, phase A (explore): {result.variant}",
            "",
            f"> {NOTICE}. Exploration only: nothing was replayed, no detection result exists.",
            "",
            f"- forecaster: `{doc['forecaster']['name']}` `{doc['forecaster']['version']}`",
            f"- residuals sha256 (CSV text): `{doc['residuals_sha256']}`",
            f"- folds sha256: `{doc['folds_sha256']}`",
            f"- leaks_2018.csv sha256: `{doc['leaks_2018_sha256']}`",
            f"- protocol file sha256: `{protocol_sha256}`; exploration rules sha256: "
            f"`{doc['exploration_rules_sha256']}`",
            f"- explore.json sha256: `{result.sha256}`",
            f"- code: `{_commit()}`",
            f"- windows: {doc['windows']}; clean windows per mask: {doc['clean_windows']}",
            "",
            "## Sigma, resolution and autocorrelation",
            "",
            f"Mask for sigma: `{sigma['mask_policy']}`. Floor: {FLOOR_RULE} = "
            f"{_num(1000 * sigma['coarsest_step_m'])} mm / √12 = "
            f"{_num(1000 * sigma['floor_m'])} mm.",
            "",
            _markdown(sensors),
            "",
            f"Lag-1 autocorrelation median {_num(guidance['lag1_median'])}; inflation "
            f"(1+φ)/(1-φ) median {_num(guidance['inflation_median'])}, max "
            f"{_num(guidance['inflation_max'])} (bounds of the h grid, never a choice in it).",
            "",
            "## Abrupt leaks: displacement and persistence (ADR-008 §D)",
            "",
            _markdown(leaks),
            "",
            f"Persistence median {_num(v.median)} (needs ≥ {v.median_at_least}), minimum "
            f"{_num(v.minimum)} (needs ≥ {v.minimum_at_least}): "
            f"**{'PASS' if v.passed else 'FAIL'}**.",
            "",
            "## δ_N and k_N = δ_N / 2",
            "",
            _markdown(deltas),
            "",
        ]
    )


def write_explore(result: ExploreResult, out_dir: Path, protocol_sha256: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / EXPLORE_JSON).write_bytes(_canonical(result.document))
    (out_dir / EXPLORE_MD).write_text(explore_markdown(result, protocol_sha256), encoding="utf-8")


# --- Phase B ---------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class OperatingPoint:
    """One budget: the leave-one-leak-out summary of the ``(h, N)`` choice, the cap and release
    stage at its final cell and the operating point after it (``None`` if no cell met the
    budget)."""

    budget: float
    lolo: LoloSummary
    cap_release: pd.DataFrame | None
    point: Cell | None
    result: Layer1Result | None
    recoveries: list[Recovery]


@dataclass(frozen=True, eq=False)
class SweepResult:
    variant: str
    explore: ExploreResult
    explore_reproduced: bool
    table: pd.DataFrame
    pareto: pd.DataFrame
    points: list[OperatingPoint]
    k_sensitivity: pd.DataFrame
    floors: pd.DataFrame | None
    artefact: CalibrationArtefact | None
    artefact_note: str
    seconds: float


def _check_registered_k(rules: SweepRules, variant: str, found: ExploreResult) -> dict[int, float]:
    registered = rules.k_by_n.get(variant)
    if registered is None:
        raise ValueError(f"the sweep section has no k_by_n for variant {variant!r}")
    k_by_n = {int(n): k for n, k in registered.items()}
    if k_by_n != found.k_by_n:
        raise ValueError(
            f"k_by_n registered for {variant} is {k_by_n}, but the exploration gives "
            f"{found.k_by_n}: the registration must copy the exploration"
        )
    return k_by_n


def _recovery_summary(recoveries: Sequence[Recovery], target: timedelta) -> dict[str, object]:
    detected = [r for r in recoveries if r.detected_at is not None]
    hours = [math.inf if r.recovery is None else r.recovery / timedelta(hours=1) for r in detected]
    return {
        "repaired": len(recoveries),
        "repaired_detected": len(detected),
        "recovered_within_target": sum(
            r.recovery is not None and r.recovery <= target for r in detected
        ),
        "median_recovery_h": statistics.median(hours) if hours else math.nan,
    }


def _inf_if_nan(value: float) -> float:
    return math.inf if math.isnan(value) else value


def _cap_and_release(
    z: np.ndarray,
    index: pd.DatetimeIndex,
    leaks: pd.DataFrame,
    base: Cell,
    rules: SweepRules,
    budget: float,
    cache: dict[Cell, list[Opening]],
    *,
    period: timedelta,
    match: MatchRule,
) -> tuple[pd.DataFrame, Cell | None, Layer1Result | None, list[Recovery]]:
    """The ``c`` x ``h_close / h`` grid at ``(k, h, N)`` of ``base``: replay (cached),
    re-evaluate, recovery after the repairs, and the registered choice: within the budget, most
    leaks detected, most repaired leaks recovered within the target, smallest median recovery,
    fewest repeat openings, then smaller ``c`` and larger ``h_close``."""
    cells = [
        Cell(base.k, base.h, base.n_min, c, ratio * base.h)
        for c in rules.c_values
        for ratio in rules.h_close_ratios
    ]
    missing = [cell for cell in cells if cell not in cache]
    cache.update(replay_cells(z, index, missing))
    results = evaluate_cells(
        {cell: cache[cell] for cell in cells}, leaks, period=period, match=match
    )
    target = timedelta(hours=rules.recovery_target_hours)
    paths = {c: trajectory(z, k=base.k, h=base.h, c=c) for c in rules.c_values}
    recoveries = {
        cell: recovery_times(
            paths[cell.c], index, leaks, results[cell], cache[cell], h_close=cell.h_close
        )
        for cell in cells
    }
    table = cell_table(results)
    summaries = pd.DataFrame([_recovery_summary(recoveries[cell], target) for cell in cells])
    table = pd.concat([table, summaries], axis=1)
    table.insert(0, "h_close_ratio", [cell.h_close / cell.h for cell in cells])
    eligible = [
        (cell, row)
        for cell, (_, row) in zip(cells, table.iterrows(), strict=True)
        if float(row["false_alarms_per_week"]) <= budget
    ]
    if not eligible:
        table["chosen"] = False
        return table, None, None, []
    chosen, _ = min(
        eligible,
        key=lambda pair: (
            -int(pair[1]["detected"]),
            -int(pair[1]["recovered_within_target"]),
            _inf_if_nan(float(pair[1]["median_recovery_h"])),
            int(pair[1]["repeat_openings"]),
            pair[0].c,
            -pair[0].h_close,
        ),
    )
    table["chosen"] = [cell == chosen for cell in cells]
    return table, chosen, results[chosen], recoveries[chosen]


def _floor_table(
    found: ExploreResult,
    rules: SweepRules,
    mask_policy: MaskPolicy,
    point: Cell,
    residuals: pd.DataFrame,
    leaks: pd.DataFrame,
    *,
    period: timedelta,
    match: MatchRule,
) -> pd.DataFrame:
    """False alarms per week of each sensor alone (``N = 1``) at the operating point, for the
    sigma floors Δ/√12, Δ (Δ of the coarsest sensor) and the registered extra floors."""
    coarsest = float(found.document["sigma"]["coarsest_step_m"])
    floors = {
        f"Δ/√12 = {_num(1000 * found.floor_m)} mm": found.floor_m,
        f"Δ = {_num(1000 * coarsest)} mm": coarsest,
        **{f"{_num(1000 * f)} mm": f for f in rules.extra_floors_m},
    }
    index = pd.DatetimeIndex(residuals.index)
    sensors = [str(s) for s in residuals.columns]
    mad = found.mad_sigma[mask_policy]
    single = Cell(point.k, point.h, 1, point.c, point.h_close)
    columns: dict[str, list[float]] = {}
    totals: dict[str, float] = {}
    for label, floor in floors.items():
        sigma = {s: max(floor, float(mad[s])) for s in sensors}
        openings = replay_cells(z_matrix(residuals, sigma), index, [single])[single]
        per_sensor = []
        for j in range(len(sensors)):
            times = [o.timestamp for o in openings if j in o.sensors]
            per_sensor.append(
                evaluate(times, leaks, period=period, match=match).false_alarms_per_week
            )
        columns[label] = per_sensor
        totals[label] = evaluate(
            [o.timestamp for o in openings], leaks, period=period, match=match
        ).false_alarms_per_week
    table = pd.DataFrame(
        {"sensor": sensors, "sigma MAD (mm)": [1000 * float(mad[s]) for s in sensors], **columns}
    )
    total = {"sensor": "any (N = 1)", "sigma MAD (mm)": math.nan, **totals}
    return pd.concat([table, pd.DataFrame([total])], ignore_index=True)


def run_sweep(
    protocol: Protocol,
    variant: str,
    oof: OofData,
    leaks: pd.DataFrame,
    leaks_sha256: str,
    *,
    provenance: Provenance,
) -> SweepResult:
    """Phase B on one variant (module docstring); pure, writes nothing."""
    started = time.perf_counter()
    if isinstance(protocol.sweep, str):
        raise ValueError("the sweep section of the protocol is not registered yet: run explore")
    rules = protocol.sweep
    found = explore(protocol, variant, oof, leaks, leaks_sha256)
    k_by_n = _check_registered_k(rules, variant, found)
    reproduced = rules.explore_sha256.get(variant) == found.sha256
    index = pd.DatetimeIndex(oof.observed.index)
    z = found.z.to_numpy(dtype=np.float64)
    period = WINDOW * len(index)
    span = (index[0].to_pydatetime(), index[-1].to_pydatetime())
    match = MatchRule(
        {kind: timedelta(hours=hours) for kind, hours in rules.max_delay_hours.items()}
    )

    def grid(multiplier: float) -> list[Cell]:
        return [
            Cell(k * multiplier, h, n, rules.base_c, rules.base_h_close_ratio * h)
            for n, k in sorted(k_by_n.items())
            for h in rules.h_values
        ]

    cells = grid(1.0)
    cache = replay_cells(z, index, cells)
    results = evaluate_cells(cache, leaks, period=period, match=match)
    table = cell_table(results)
    lolo = leave_one_leak_out(
        cache,
        results,
        leaks,
        period=period,
        span=span,
        match=match,
        budgets=rules.budgets_per_week,
    )
    points = []
    for summary in lolo:
        if summary.final is None:
            points.append(OperatingPoint(summary.budget, summary, None, None, None, []))
            continue
        stage, point, result, recoveries = _cap_and_release(
            z, index, leaks, summary.final, rules, summary.budget, cache, period=period, match=match
        )
        points.append(OperatingPoint(summary.budget, summary, stage, point, result, recoveries))
    sensitivity = []
    for multiplier in rules.k_sensitivity:
        openings = replay_cells(z, index, grid(multiplier))
        part = cell_table(evaluate_cells(openings, leaks, period=period, match=match))
        part.insert(0, "k_multiplier", multiplier)
        labels: dict[object, list[str]] = {}
        for budget in rules.budgets_per_week:
            row = select(part, budget)
            if row is not None:
                labels.setdefault(row.name, []).append(f"{budget:g}")
        part["chosen_for_budget"] = [" ".join(labels.get(i, [])) for i in part.index]
        sensitivity.append(part)
    primary = next(p for p in points if p.budget == rules.primary_budget_per_week)
    floors = None
    artefact = None
    if primary.point is None:
        note = (
            "not written: no cell within the primary budget "
            f"({rules.primary_budget_per_week:g}/week)"
        )
    else:
        floors = _floor_table(
            found,
            rules,
            protocol.exploration.sigma.mask_policy,
            primary.point,
            oof.residuals,
            leaks,
            period=period,
            match=match,
        )
        if not found.verdict.passed:
            note = (
                "not written: the forecaster fails the pre-registered persistence criterion "
                "(ADR-008 §D); the calibration stays a development one"
            )
        else:
            artefact = _artefact(protocol, variant, oof, leaks, found, primary.point, provenance)
            note = f"candidate for the primary budget ({rules.primary_budget_per_week:g}/week)"
    return SweepResult(
        variant=variant,
        explore=found,
        explore_reproduced=reproduced,
        table=table,
        pareto=pareto_front(table),
        points=points,
        k_sensitivity=pd.concat(sensitivity, ignore_index=True),
        floors=floors,
        artefact=artefact,
        artefact_note=note,
        seconds=time.perf_counter() - started,
    )


def _artefact(
    protocol: Protocol,
    variant: str,
    oof: OofData,
    leaks: pd.DataFrame,
    found: ExploreResult,
    point: Cell,
    provenance: Provenance,
) -> CalibrationArtefact:
    """``calibrate()`` on the residuals the sigma mask keeps; its sigma must be the one the
    sweep used, float for float (else the deployed detector would not be the swept one)."""
    index = pd.DatetimeIndex(oof.observed.index)
    keep = clean_windows(index, leaks, protocol.exploration.sigma.mask_policy)
    values = oof.residuals.to_numpy(dtype=np.float64)[keep]
    series = {str(s): values[:, j].tolist() for j, s in enumerate(oof.residuals.columns)}
    artefact = calibrate(
        series,
        provenance=provenance,
        forecaster=protocol.inputs[variant].forecaster,
        residuals_sha256=oof.residuals_sha256,
        folds_sha256=oof.folds_sha256,
        sigma_floor_m=found.floor_m,
        k=point.k,
        h=point.h,
        n_min=point.n_min,
        c=point.c,
        h_close=point.h_close,
    )
    if dict(artefact.sigma_m) != found.sigma:
        raise RuntimeError("the artefact sigma differs from the sigma the sweep used")
    return artefact


def _cell_text(cell: Cell | None) -> str:
    if cell is None:
        return "—"
    return (
        f"k={_num(cell.k)} h={_num(cell.h)} N={cell.n_min} c={_num(cell.c)} "
        f"h_close={_num(cell.h_close)}"
    )


def _count(result: Layer1Result) -> str:
    by = result.by_type
    return (
        f"{result.detected}/{result.total} (abrupt {by['abrupt'].detected}/{by['abrupt'].total}, "
        f"incipient {by['incipient'].detected}/{by['incipient'].total})"
    )


def sweep_markdown(result: SweepResult, protocol: Protocol, protocol_sha256: str) -> str:
    assert isinstance(protocol.sweep, SweepRules)
    rules = protocol.sweep
    found = result.explore
    doc = found.document
    rows = []
    for p in result.points:
        s = p.lolo
        recovery = _recovery_summary(p.recoveries, timedelta(hours=rules.recovery_target_hours))
        rows.append(
            {
                "budget /week": f"{p.budget:g}",
                "in-sample (h, N)": _cell_text(s.in_sample),
                "in-sample detected": f"{s.in_sample_detected}/{s.total}",
                "LOLO detected": f"{s.lolo_detected}/{s.total}",
                "distinct LOLO cells": s.distinct_cells,
                "stable": "yes" if s.stable else "no",
                "operating point": _cell_text(p.point),
                "detected": "—" if p.result is None else _count(p.result),
                "false alarms /week": math.nan
                if p.result is None
                else p.result.false_alarms_per_week,
                "repeat openings": "—" if p.result is None else p.result.repeat_openings,
                "recovered ≤ target": (
                    f"{recovery['recovered_within_target']}/{recovery['repaired_detected']}"
                ),
            }
        )
    lines = [
        f"# Calibration experiment, phase B (sweep): {result.variant}",
        "",
        f"> {NOTICE}.",
        "",
        f"- forecaster: `{doc['forecaster']['name']}` `{doc['forecaster']['version']}`",
        f"- residuals sha256 (CSV text): `{doc['residuals_sha256']}`; folds sha256: "
        f"`{doc['folds_sha256']}`; leaks_2018.csv sha256: `{doc['leaks_2018_sha256']}`",
        f"- protocol file sha256: `{protocol_sha256}` (sweep registered {rules.registered})",
        f"- explore.json reproduced the registered hash: "
        f"{'yes' if result.explore_reproduced else 'NO'} (`{found.sha256}`)",
        f"- code: `{_commit()}`; run time {result.seconds:.0f} s",
        f"- persistence (ADR-008 §D): median {_num(found.verdict.median)}, minimum "
        f"{_num(found.verdict.minimum)}: **{'PASS' if found.verdict.passed else 'FAIL'}**",
        f"- k by N (registered): {found.k_by_n}; base c = {rules.base_c:g}, base h_close/h = "
        f"{rules.base_h_close_ratio:g}; matching rule: max delay {dict(rules.max_delay_hours)} h",
        "",
        "## Operating point per budget",
        "",
        "Selection: within the budget, most leaks detected, then smallest median delay from "
        "start; ties to the larger h, then the larger N. Leave-one-leak-out (LOLO) repeats the "
        "selection without each leak; an unstable choice falls back to the most conservative of "
        "the chosen cells. The cap c and h_close are then chosen on the recovery after the 2018 "
        "repairs and the cell is re-evaluated.",
        "",
        _markdown(pd.DataFrame(rows)),
        "",
        "## Pareto front (main grid, base c and h_close)",
        "",
        _markdown(
            result.pareto[
                [
                    "k",
                    "h",
                    "n_min",
                    "false_alarms_per_week",
                    "detected",
                    "detected_abrupt",
                    "detected_incipient",
                    "median_delay_start_h",
                    "repeat_openings",
                    "missed",
                ]
            ]
        ),
        "",
        "## k sensitivity (reported only, never used to choose)",
        "",
    ]
    chosen = result.k_sensitivity[result.k_sensitivity["chosen_for_budget"] != ""]
    lines.append(
        _markdown(
            chosen[
                [
                    "k_multiplier",
                    "chosen_for_budget",
                    "k",
                    "h",
                    "n_min",
                    "false_alarms_per_week",
                    "detected",
                    "median_delay_start_h",
                ]
            ]
        )
        if len(chosen)
        else "No cell within any budget."
    )
    lines += ["", "## False alarms per week per sensor (N = 1) for several sigma floors", ""]
    lines.append("—" if result.floors is None else _markdown(result.floors))
    lines += ["", "## Calibration artefact", ""]
    if result.artefact is None:
        lines.append(result.artefact_note + ".")
    else:
        digest = hashlib.sha256(canonical_bytes(result.artefact)).hexdigest()
        lines.append(
            f"{result.artefact_note}: provenance `{result.artefact.provenance}`, "
            f"is_mock = {str(result.artefact.is_mock).lower()}, sha256 `{digest}`. The final "
            "artefact waits for the team's budget (Q-TEAM-03)."
        )
    lines.append("")
    return "\n".join(lines)


def write_sweep(
    result: SweepResult, protocol: Protocol, out_dir: Path, protocol_sha256: str
) -> Path | None:
    """Write the tables, the report and the candidate artefact; return the artefact path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result.table.to_csv(out_dir / "sweep.csv", index=False)
    result.pareto.to_csv(out_dir / "pareto.csv", index=False)
    result.k_sensitivity.to_csv(out_dir / "k_sensitivity.csv", index=False)
    lolo_rows = [
        {
            "budget_per_week": p.budget,
            "held_out": fold.pipe,
            "type": fold.type,
            "chosen": _cell_text(fold.chosen),
            "detected": fold.detected,
            "delay_from_start_h": math.nan
            if fold.delay_from_start is None
            else fold.delay_from_start / timedelta(hours=1),
        }
        for p in result.points
        for fold in p.lolo.folds
    ]
    pd.DataFrame(lolo_rows).to_csv(out_dir / "lolo.csv", index=False)
    stages = [
        p.cap_release.assign(budget_per_week=p.budget)
        for p in result.points
        if p.cap_release is not None
    ]
    if stages:
        pd.concat(stages, ignore_index=True).to_csv(out_dir / "recovery.csv", index=False)
    if result.floors is not None:
        result.floors.to_csv(out_dir / "floors.csv", index=False)
    (out_dir / EXPLORE_JSON).write_bytes(_canonical(result.explore.document))
    (out_dir / REPORT_MD).write_text(
        sweep_markdown(result, protocol, protocol_sha256), encoding="utf-8"
    )
    if result.artefact is None:
        return None
    path = out_dir / f"calibration.{result.artefact.forecaster.name}.candidate.json"
    write_artefact(result.artefact, path)
    return path


# --- Command line ----------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="phase", required=True)
    data_dir = os.environ.get("SENTINEL_DATA_DIR")
    for name in ("explore", "sweep"):
        p = sub.add_parser(name)
        p.add_argument("--protocol", type=Path, required=True)
        p.add_argument("--variant", required=True)
        p.add_argument("--residuals-dir", type=Path, required=True)
        p.add_argument("--out-dir", type=Path, required=True)
        p.add_argument(
            "--data-dir",
            type=Path,
            default=Path(data_dir).expanduser() if data_dir else None,
            help="data root with v1/leaks_2018.csv (default: $SENTINEL_DATA_DIR)",
        )
        p.add_argument("--registry", type=Path, default=Path("config/sensors.battledim.json"))
        if name == "sweep":
            p.add_argument(
                "--provenance",
                required=True,
                choices=["synthetic", "dev-baseline-2018", "battledim-2018-oof"],
            )
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.data_dir is None:
        raise SystemExit("pass --data-dir or set SENTINEL_DATA_DIR")
    refuse_2019(args.protocol, args.residuals_dir, args.out_dir, args.data_dir, args.registry)
    protocol, protocol_sha256 = load_protocol(args.protocol)
    registry = SensorRegistry.load(args.registry)
    oof = load_oof_residuals(args.residuals_dir.expanduser(), registry)
    leaks, leaks_sha256 = load_leaks_2018(args.data_dir.expanduser())
    out_dir = args.out_dir.expanduser()
    if args.phase == "explore":
        found = explore(protocol, args.variant, oof, leaks, leaks_sha256)
        write_explore(found, out_dir, protocol_sha256)
        print(f"{out_dir / EXPLORE_MD}: explore.json sha256 {found.sha256}")
        print(f"persistence {'PASS' if found.verdict.passed else 'FAIL'}; k by N {found.k_by_n}")
        return 0
    result = run_sweep(protocol, args.variant, oof, leaks, leaks_sha256, provenance=args.provenance)
    path = write_sweep(result, protocol, out_dir, protocol_sha256)
    print(f"{out_dir / REPORT_MD}: {len(result.table)} cells in {result.seconds:.0f} s")
    print(f"artefact: {path if path else result.artefact_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
