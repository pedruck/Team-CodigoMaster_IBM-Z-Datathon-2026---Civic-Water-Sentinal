"""The calibration experiment end to end on synthetic files (BE-012j).

The data, the leaks, the forecaster and every protocol number below are invented for the tests;
none comes from data and none is a result. The synthetic residuals are i.i.d. noise plus
constant drops, far easier than the real ones: the tests check the mechanics (the two phases,
the registration checks, the outputs, the artefact), never "these parameters work".
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import BaseModel, ValidationError

from sentinel.detection.calibration import CalibrationArtefact
from sentinel.detection.offline.experiment import (
    NOT_REGISTERED,
    NOTICE,
    DeltaRule,
    Exploration,
    ExploreResult,
    PersistenceRule,
    Protocol,
    SigmaRule,
    SweepRules,
    VariantInput,
    explore,
    load_protocol,
    main,
    run_sweep,
    write_explore,
    write_sweep,
)
from sentinel.detection.offline.oof import load_oof_residuals
from sentinel.detection.offline.persistence import load_leaks_2018
from tests.detection.oof_files import (
    FORECASTER,
    Drop,
    folds_text,
    long_table,
    registry,
    wide_frames,
    write_data_dir,
    write_variant,
)

REPO = Path(__file__).resolve().parents[2]
VARIANT = "synthetic-variant"
# The matching windows do not overlap: layer 1 credits an opening to the first undetected leak
# by start, so an overlap would move a detection from one leak to another.
LEAKS = [
    ("pA", "2018-03-04 00:00:00", "2018-03-08 00:00:00", "abrupt", "2018-03-04 00:00:00", True),
    ("pB", "2018-03-10 00:00:00", "2018-03-13 00:00:00", "abrupt", "2018-03-10 00:00:00", True),
    ("pC", "2018-03-14 00:00:00", "2018-03-16 23:55:00", "incipient", "2018-03-15 00:00:00", False),
]
DROPS = [
    Drop("2018-03-04 00:00", "2018-03-08 00:00", {"P1": 0.2, "P2": 0.1}),
    Drop("2018-03-10 00:00", "2018-03-13 00:00", {"P3": 0.15, "P4": 0.08}),
    Drop("2018-03-14 00:00", "2018-03-16 23:55", {"P4": 0.03}),
]


def exploration_rules() -> dict[str, Any]:
    return {
        "registered": "2026-10-17",
        "sigma": {
            "mask_policy": "none",
            "floor": "quantization step / sqrt(12) of the coarsest sensor",
            "rationale": "invented for the test",
        },
        "persistence": {
            "baseline_hours": 24,
            "initial_hours": 1,
            "remaining_after_hours": 24,
            "n_top": 2,
            "median_at_least": 0.5,
            "minimum_at_least": 0.25,
            "rationale": "invented for the test",
        },
        "delta": {
            "sustained_hours": 24,
            "max_rank": 2,
            "k_decimals": 2,
            "rationale": "invented for the test",
        },
    }


def sweep_rules(explored: ExploreResult) -> dict[str, Any]:
    return registered_rules(explored.sha256, explored.k_by_n)


def registered_rules(explore_sha256: str, k_by_n: Mapping[int, float]) -> dict[str, Any]:
    return {
        "registered": "2026-10-17",
        "explore_sha256": {VARIANT: explore_sha256},
        "k_by_n": {VARIANT: {str(n): k for n, k in k_by_n.items()}},
        "k_sensitivity": [0.5, 2.0],
        "h_values": [5.0, 10.0, 20.0],
        "base_c": 2.0,
        "base_h_close_ratio": 0.5,
        "c_values": [1.5, 2.0],
        "h_close_ratios": [0.5, 0.75],
        "max_delay_hours": {"abrupt": 168, "incipient": 168},
        "budgets_per_week": [1.0, 5.0],
        "primary_budget_per_week": 5.0,
        "recovery_target_hours": 24,
        "extra_floors_m": [0.005],
        "rationale": "invented for the test",
    }


class Files:
    """A synthetic data dir, one OOF variant folder and a protocol pinned to them."""

    def __init__(self, root: Path, drops: Sequence[Drop] = DROPS) -> None:
        self.data_dir = root / "data"
        write_data_dir(self.data_dir, LEAKS)
        observed, expected = wide_frames(
            start="2018-03-01 00:00", windows=16 * 288, seed=3, noise_m=0.02, drops=drops
        )
        self.residuals_dir = root / "oof" / VARIANT
        residuals_sha, folds_sha = write_variant(
            self.residuals_dir,
            long_table(observed, expected),
            folds=folds_text(pd.DatetimeIndex(observed.index)),
        )
        self.inputs = {
            VARIANT: {
                "forecaster": dict(FORECASTER),
                "residuals_sha256": residuals_sha,
                "folds_sha256": folds_sha,
            }
        }
        self.protocol_path = root / "protocol.json"
        self.out_dir = root / "out"

    def protocol(self, sweep: Any = NOT_REGISTERED) -> Protocol:
        document = {
            "protocol": "cusum-calibration/1",
            "exploration": exploration_rules(),
            "inputs": self.inputs,
            "sweep": sweep,
        }
        self.protocol_path.write_text(json.dumps(document), encoding="utf-8")
        return load_protocol(self.protocol_path)[0]

    def load(self) -> tuple[Any, Any, str]:
        oof = load_oof_residuals(self.residuals_dir, registry())
        leaks, digest = load_leaks_2018(self.data_dir)
        return oof, leaks, digest


@pytest.fixture
def files(work: Path) -> Files:
    return Files(work)


# --- The protocol ----------------------------------------------------------------------------


def test_the_committed_protocol_is_valid_and_pins_the_delivered_residuals() -> None:
    protocol, _ = load_protocol(REPO / "config" / "calibration-protocol.xgboost-33.json")

    delivered = protocol.inputs["with-neighbours"]
    assert (delivered.forecaster.name, delivered.forecaster.version) == ("xgboost-33", "0.1.0")
    assert set(protocol.inputs) == {"with-neighbours", "without-neighbours"}


@pytest.mark.parametrize(
    "model",
    [Protocol, Exploration, SigmaRule, PersistenceRule, DeltaRule, VariantInput, SweepRules],
)
def test_no_field_of_the_protocol_has_a_default(model: type[BaseModel]) -> None:
    optional = [name for name, info in model.model_fields.items() if not info.is_required()]

    assert optional == []


def test_a_missing_or_unknown_field_is_refused(files: Files) -> None:
    document = json.loads(json.dumps({"protocol": "cusum-calibration/1", "inputs": files.inputs}))
    with pytest.raises(ValidationError):
        Protocol.model_validate(document | {"sweep": NOT_REGISTERED})  # no exploration
    rules = exploration_rules()
    rules["sigma"]["typo"] = 1
    with pytest.raises(ValidationError, match="typo"):
        Protocol.model_validate(document | {"exploration": rules, "sweep": NOT_REGISTERED})


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"h_values": [10.0, 5.0]}, "ascending"),
        ({"base_c": 3.0}, "base_c"),
        ({"base_h_close_ratio": 0.25}, "base_h_close_ratio"),
        ({"primary_budget_per_week": 2.0}, "primary_budget"),
        ({"k_sensitivity": [1.0]}, "main grid"),
        ({"max_delay_hours": {"abrupt": 1}}, "leak types"),
        ({"c_values": [1.0, 2.0]}, "greater than 1"),
    ],
)
def test_inconsistent_sweep_rules_are_refused(
    files: Files, change: dict[str, Any], match: str
) -> None:
    oof, leaks, digest = files.load()
    explored = explore(files.protocol(), VARIANT, oof, leaks, digest)

    with pytest.raises(ValidationError, match=match):
        SweepRules.model_validate(sweep_rules(explored) | change)


# --- Phase A ---------------------------------------------------------------------------------


def test_explore_measures_sigma_persistence_and_k_without_replaying(files: Files) -> None:
    oof, leaks, digest = files.load()

    explored = explore(files.protocol(), VARIANT, oof, leaks, digest)

    assert set(explored.k_by_n) == {1, 2}
    assert explored.k_by_n[1] > explored.k_by_n[2] > 0  # the N-th sensor moves less
    assert explored.verdict.passed  # constant drops persist entirely
    assert [d.pipe for d in explored.displacements] == ["pA", "pB"]
    assert explored.displacements[0].top_sensors == ("P1", "P2")
    assert explored.floor_m == pytest.approx(0.01 / 12**0.5)
    assert set(explored.mad_sigma.columns) == {
        "none",
        "reparados",
        "reparados+crescimento",
        "todos",
    }
    assert "commit" not in json.dumps(explored.document)  # deterministic: no run metadata
    again = explore(files.protocol(), VARIANT, oof, leaks, digest)
    assert again.sha256 == explored.sha256


def test_explore_refuses_files_the_protocol_does_not_pin(files: Files) -> None:
    oof, leaks, digest = files.load()
    files.inputs[VARIANT]["residuals_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="pins"):
        explore(files.protocol(), VARIANT, oof, leaks, digest)
    with pytest.raises(ValueError, match="not in the protocol"):
        explore(files.protocol(), "other-variant", oof, leaks, digest)


def test_explore_writes_its_json_and_report(files: Files) -> None:
    oof, leaks, digest = files.load()
    explored = explore(files.protocol(), VARIANT, oof, leaks, digest)

    write_explore(explored, files.out_dir, "f" * 64)

    assert (files.out_dir / "explore.json").read_bytes()
    report = (files.out_dir / "explore.md").read_text(encoding="utf-8")
    assert NOTICE in report
    assert "nothing was replayed" in report


# --- Phase B ---------------------------------------------------------------------------------


def registered(files: Files) -> tuple[Protocol, Any, Any, str]:
    oof, leaks, digest = files.load()
    explored = explore(files.protocol(), VARIANT, oof, leaks, digest)
    return files.protocol(sweep_rules(explored)), oof, leaks, digest


def test_sweep_refuses_a_protocol_whose_sweep_is_not_registered(files: Files) -> None:
    oof, leaks, digest = files.load()

    with pytest.raises(ValueError, match="not registered"):
        run_sweep(files.protocol(), VARIANT, oof, leaks, digest, provenance="synthetic")


def test_sweep_refuses_a_k_that_is_not_the_explored_one(files: Files) -> None:
    oof, leaks, digest = files.load()
    rules = sweep_rules(explore(files.protocol(), VARIANT, oof, leaks, digest))
    rules["k_by_n"][VARIANT]["1"] += 0.01

    with pytest.raises(ValueError, match="must copy the exploration"):
        run_sweep(files.protocol(rules), VARIANT, oof, leaks, digest, provenance="synthetic")


def test_sweep_chooses_an_operating_point_and_writes_the_candidate_artefact(files: Files) -> None:
    protocol, oof, leaks, digest = registered(files)

    result = run_sweep(protocol, VARIANT, oof, leaks, digest, provenance="synthetic")

    assert result.explore_reproduced
    assert len(result.table) == 2 * 3  # N in {1, 2} x three h
    assert len(result.k_sensitivity) == 2 * 2 * 3
    primary = next(p for p in result.points if p.budget == 5.0)
    assert primary.point is not None and primary.result is not None
    # Both abrupt leaks; the incipient one (z about 1) is below k_1: not a claim on real data.
    assert primary.result.by_type["abrupt"].detected == 2
    assert primary.lolo.total == 3
    assert primary.cap_release is not None and int(primary.cap_release["chosen"].sum()) == 1
    artefact = result.artefact
    assert artefact is not None
    assert artefact.provenance == "synthetic" and artefact.is_mock
    assert (artefact.k, artefact.h, artefact.n_min, artefact.c, artefact.h_close) == (
        primary.point.k,
        primary.point.h,
        primary.point.n_min,
        primary.point.c,
        primary.point.h_close,
    )
    assert dict(artefact.sigma_m) == result.explore.sigma
    assert artefact.residuals_sha256 == oof.residuals_sha256
    assert result.floors is not None and result.floors["sensor"].iloc[-1] == "any (N = 1)"

    path = write_sweep(result, protocol, files.out_dir, "f" * 64)

    assert path == files.out_dir / "calibration.test-forecaster.candidate.json"
    assert CalibrationArtefact.model_validate_json(path.read_bytes()) == artefact
    for name in ("sweep.csv", "pareto.csv", "lolo.csv", "recovery.csv", "k_sensitivity.csv"):
        assert (files.out_dir / name).stat().st_size > 0
    report = (files.out_dir / "report.md").read_text(encoding="utf-8")
    assert NOTICE in report
    assert "PASS" in report


def test_no_artefact_when_the_persistence_criterion_fails(work: Path) -> None:
    # pA's drop vanishes after 12 h although the label keeps it until its repair: the residual
    # does not persist (what a forecaster that absorbs the leak would give).
    fading = [Drop("2018-03-04 00:00", "2018-03-04 12:00", {"P1": 0.2, "P2": 0.1}), *DROPS[1:]]
    files = Files(work, fading)
    oof, leaks, digest = files.load()
    explored = explore(files.protocol(), VARIANT, oof, leaks, digest)
    assert not explored.verdict.passed
    protocol = files.protocol(sweep_rules(explored))

    result = run_sweep(protocol, VARIANT, oof, leaks, digest, provenance="synthetic")

    assert result.artefact is None
    assert "persistence" in result.artefact_note


# --- Command line ----------------------------------------------------------------------------


def test_the_command_line_runs_both_phases(
    files: Files, capsys: pytest.CaptureFixture[str]
) -> None:
    files.protocol()
    common = [
        "--protocol",
        str(files.protocol_path),
        "--variant",
        VARIANT,
        "--residuals-dir",
        str(files.residuals_dir),
        "--out-dir",
        str(files.out_dir),
        "--data-dir",
        str(files.data_dir),
        "--registry",
        str(files.residuals_dir.parent / "registry.json"),
    ]
    (files.residuals_dir.parent / "registry.json").write_text(
        json.dumps({"provisional": True, "pressure": ["P1", "P2", "P3", "P4"]})
    )

    assert main(["explore", *common]) == 0
    written = (files.out_dir / "explore.json").read_bytes()
    explored = json.loads(written)
    digest = hashlib.sha256(written).hexdigest()
    assert digest in capsys.readouterr().out
    # The registration step, as the detection owner does it: copy k_N into the sweep rules.
    files.protocol(registered_rules(digest, {d["n"]: d["k"] for d in explored["delta"] if d["k"]}))
    assert main(["sweep", *common, "--provenance", "synthetic"]) == 0

    assert (files.out_dir / "report.md").exists()
    assert "calibration.test-forecaster.candidate.json" in capsys.readouterr().out


def test_the_command_line_refuses_a_2019_path(files: Files) -> None:
    files.protocol()
    with pytest.raises(ValueError, match="2019"):
        main(
            [
                "explore",
                "--protocol",
                str(files.protocol_path),
                "--variant",
                VARIANT,
                "--residuals-dir",
                str(files.residuals_dir),
                "--out-dir",
                str(files.out_dir / "replay-2019"),
                "--data-dir",
                str(files.data_dir),
            ]
        )
