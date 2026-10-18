"""Displacement, persistence and delta at the abrupt leaks (BE-012j, ADR-008 §D).

Every ``z`` here is hand-made: constant steps on invented sensors and leaks, so each mean is
exact and the expected numbers can be worked out by hand. None of them is a result.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from sentinel.detection.offline.persistence import (
    LeakDisplacement,
    delta_by_rank,
    leak_displacement,
    load_leaks_2018,
    persistence_verdict,
)
from tests.detection.oof_files import write_data_dir

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
START = datetime(2018, 3, 1, 0, 5)
L1 = datetime(2018, 3, 3)  # abrupt
L2 = datetime(2018, 3, 4, 12)  # abrupt
SPANS = {"baseline": DAY, "initial": HOUR, "remaining_after": DAY, "sustained": DAY}


def hand_z() -> pd.DataFrame:
    """Six days, four sensors, z = 0 plus constant steps (see the comments)."""
    index = pd.date_range(START, periods=6 * 288, freq="5min")
    z = pd.DataFrame(0.0, index=index, columns=["A", "B", "C", "D"])
    t1, t2 = pd.Timestamp(L1), pd.Timestamp(L2)
    z["D"] += 1.0  # an offset present all along: the baseline removes it
    after1 = index > t1
    z.loc[after1, "A"] += 4.0
    z.loc[after1 & (index <= t1 + DAY), "B"] += 3.0  # halves after 24 h
    z.loc[index > t1 + DAY, "B"] += 1.5
    z.loc[after1, "C"] += 1.0
    after2 = index > t2
    z.loc[after2, "D"] += 2.0
    z.loc[after2, "C"] += 0.5
    return z


def leaks(**ends: datetime) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pipe": ["p1", "p9", "p2"],
            "start": [L1, datetime(2018, 3, 2), L2],
            "end": [
                ends.get("p1", datetime(2018, 3, 5)),
                datetime(2018, 3, 6),
                datetime(2018, 3, 6),
            ],
            "type": ["abrupt", "incipient", "abrupt"],
            "peak": [L1, datetime(2018, 3, 5), L2],
            "repaired_in_2018": [True, False, True],
        }
    )


def measure(z: pd.DataFrame, table: pd.DataFrame, **overrides: object) -> list[LeakDisplacement]:
    arguments: dict[str, object] = {"n_top": 3, "max_rank": 3, **SPANS, **overrides}
    return leak_displacement(z, table, **arguments)  # type: ignore[arg-type]


def test_displacements_of_the_abrupt_leaks_by_hand() -> None:
    first, second = measure(hand_z(), leaks())

    assert first.pipe == "p1"
    assert first.top_sensors == ("A", "B", "C")  # D only carries an offset: displacement 0
    assert first.initial == pytest.approx((4.0, 3.0, 1.0))
    assert first.remaining == pytest.approx((4.0, 1.5, 1.0))
    assert first.persistence == pytest.approx(6.5 / 8.0)
    assert first.sustained_sensors == ("A", "B", "C")
    assert first.sustained == pytest.approx((4.0, 3.0, 1.0))
    # p1's steps are constant from L1 + 24 h on, before p2's baseline starts: they cancel.
    assert second.pipe == "p2"
    assert second.top_sensors == ("D", "C", "A")  # A and B tie at 0: sensor order
    assert second.initial == pytest.approx((2.0, 0.5, 0.0))
    assert second.persistence == pytest.approx(1.0)
    assert second.sustained == pytest.approx((2.0, 0.5, 0.0))


def test_the_incipient_leaks_are_not_measured() -> None:
    assert [d.pipe for d in measure(hand_z(), leaks())] == ["p1", "p2"]


def test_delta_n_is_the_weakest_abrupt_leak_on_its_nth_sensor() -> None:
    deltas = delta_by_rank(measure(hand_z(), leaks()))

    assert [(d.rank, d.weakest_pipe) for d in deltas] == [(1, "p2"), (2, "p2"), (3, "p2")]
    assert [d.delta for d in deltas] == pytest.approx([2.0, 0.5, 0.0])


def test_a_rise_instead_of_a_drop_leaves_the_persistence_undefined() -> None:
    z = hand_z()
    after = z.index > pd.Timestamp(L2)
    z.loc[after, ["C", "D"]] = -3.0  # z falls on every sensor (the pressure rises)
    z.loc[after, ["A", "B"]] -= 10.0

    second = measure(z, leaks())[1]

    assert second.persistence is None


def test_the_verdict_applies_both_thresholds_and_counts_undefined_as_zero() -> None:
    measured = measure(hand_z(), leaks())

    verdict = persistence_verdict(measured, median_at_least=0.5, minimum_at_least=0.25)
    assert verdict.median == pytest.approx((6.5 / 8 + 1) / 2)
    assert verdict.minimum == pytest.approx(6.5 / 8)
    assert verdict.passed
    assert not persistence_verdict(measured, median_at_least=0.95, minimum_at_least=0.25).passed
    assert not persistence_verdict(measured, median_at_least=0.5, minimum_at_least=0.85).passed

    undefined = [*measured, LeakDisplacement("p3", L1, L2, (), (), (), None, (), ())]
    verdict = persistence_verdict(undefined, median_at_least=0.5, minimum_at_least=0.25)
    assert verdict.minimum == 0.0
    assert not verdict.passed


def test_a_leak_too_close_to_the_start_of_the_data_is_refused() -> None:
    z = hand_z().loc[datetime(2018, 3, 2, 12) :]  # p1's 24 h baseline is no longer covered

    with pytest.raises(ValueError, match="p1: the span"):
        measure(z, leaks())


def test_a_leak_repaired_before_the_last_window_measured_is_refused() -> None:
    with pytest.raises(ValueError, match="p1 is repaired"):
        measure(hand_z(), leaks(p1=L1 + 12 * HOUR))


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"n_top": 0}, "n_top"),
        ({"max_rank": 5}, "max_rank"),
        ({"initial": timedelta(0)}, "initial must be positive"),
        ({"baseline": -HOUR}, "baseline must be positive"),
    ],
)
def test_invalid_arguments_are_refused(overrides: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        measure(hand_z(), leaks(), **overrides)


LABELS = [
    ("p1", "2018-03-03 00:00:00", "2018-03-05 00:00:00", "abrupt", "2018-03-03 00:00:00", True),
]


def test_the_2018_labels_are_read_with_their_manifest_hash(work: Path) -> None:
    digest = write_data_dir(work, LABELS)

    table, found = load_leaks_2018(work)

    assert found == digest
    assert table["start"].tolist() == [pd.Timestamp("2018-03-03")]
    assert pd.api.types.is_datetime64_any_dtype(table["end"])


def test_labels_that_differ_from_the_manifest_are_refused(work: Path) -> None:
    write_data_dir(work, LABELS)
    path = work / "v1" / "leaks_2018.csv"
    path.write_text(path.read_text().replace("p1", "p7"))

    with pytest.raises(ValueError, match="MANIFEST"):
        load_leaks_2018(work)


def test_a_data_dir_that_names_2019_is_refused(work: Path) -> None:
    with pytest.raises(ValueError, match="2019"):
        load_leaks_2018(work / "data-2019")
