"""CUSUM detector through the ``Detector`` port (contract §3, ADR-007; BE-012a/b/c slices).

The calibration numbers below are invented for the tests; they are not results.
"""

from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from typing import Literal, NoReturn

import pytest

from sentinel.detection.calibration import CalibrationArtefact, Provenance
from sentinel.detection.cusum import CusumDetector
from sentinel.detection.interface import DetectionError
from sentinel.schemas.detections import CusumEvidence, DetectionResult, SensorResidual
from tests.conftest import INTERVAL, ts

SENSORS = ("P1", "P2", "P3", "P4")
EXPECTED = dict.fromkeys(SENSORS, 50.0)


def make_detector(n_min: int = 2, provenance: Provenance = "synthetic") -> CusumDetector:
    """sigma 0.1 m: a 0.5 m drop gives d = 5, so S grows by 4.5 per window; cap c*h = 16."""
    return CusumDetector(
        CalibrationArtefact(
            provenance=provenance,
            sigma_m=dict.fromkeys(SENSORS, 0.1),
            k=0.5,
            h=8.0,
            n_min=n_min,
            c=2.0,
            h_close=4.0,
        ),
        interval=INTERVAL,
    )


def test_no_deviation_is_normal_without_alerts() -> None:
    result = make_detector().evaluate(EXPECTED, EXPECTED, ts(0))

    assert result.state == "normal"
    assert result.alerts == ()
    assert {s: r.cusum for s, r in result.residuals.items()} == dict.fromkeys(SENSORS, 0.0)


def test_pressure_drop_accumulates_cusum_across_windows() -> None:
    detector = make_detector()
    low = {**EXPECTED, "P1": 49.5}

    first = detector.evaluate(low, EXPECTED, ts(0))
    second = detector.evaluate(low, EXPECTED, ts(1))

    assert first.residuals["P1"] == SensorResidual(residual_m=-0.5, z=-5.0, cusum=4.5)
    assert second.residuals["P1"].cusum == pytest.approx(9.0)


def test_alert_opens_when_n_sensors_cross_h() -> None:
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}

    before = detector.evaluate(low, EXPECTED, ts(0))  # S = 4.5 < h = 8
    opened = detector.evaluate(low, EXPECTED, ts(1))  # S = 9.0 > h

    assert before.alerts == ()
    (alert,) = opened.alerts
    assert alert.cusum == CusumEvidence(statistic=9.0, threshold=8.0)
    assert [(s.sensor, s.deviation_m, s.z) for s in alert.deviating_sensors] == [
        ("P1", -0.5, -5.0),
        ("P2", -0.5, -5.0),
    ]


def test_persistent_drop_opens_one_alert_and_stays_in_alarm() -> None:
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}

    results = [detector.evaluate(low, EXPECTED, ts(i)) for i in range(10)]

    assert [len(r.alerts) for r in results] == [0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    assert [r.state for r in results] == ["normal"] + ["alarm"] * 9


def test_fewer_than_n_sensors_above_h_is_suspect_without_alert() -> None:
    detector = make_detector(n_min=3)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}

    results = [detector.evaluate(low, EXPECTED, ts(i)) for i in range(3)]

    assert [r.state for r in results] == ["normal", "suspect", "suspect"]
    assert all(r.alerts == () for r in results)


@pytest.mark.parametrize("provenance", ["synthetic", "dev-baseline-2018", "battledim-2018-oof"])
def test_incomplete_detector_is_always_labelled_mock(provenance: Provenance) -> None:
    detector = make_detector(provenance=provenance)

    result = detector.evaluate(EXPECTED, EXPECTED, ts(0))

    assert detector.info.is_mock and result.model.is_mock


def test_anomaly_score_is_nth_largest_cusum() -> None:
    detector = make_detector(n_min=2)
    # S after one window: P1 = 5 - 0.5 = 4.5, P2 = 3 - 0.5 = 2.5, P3 = P4 = 0
    result = detector.evaluate({**EXPECTED, "P1": 49.5, "P2": 49.7}, EXPECTED, ts(0))

    assert result.anomaly_score == pytest.approx(2.5)


def test_diagnostics_report_the_calibration_in_use() -> None:
    result = make_detector(n_min=2).evaluate(EXPECTED, EXPECTED, ts(0))

    assert result.diagnostics == {
        "k": 0.5,
        "h": 8.0,
        "n_min": 2,
        "c": 2.0,
        "cap": 16.0,  # c*h, the bound on S
        "h_close": 4.0,
        "provenance": "synthetic",
        "skipped_windows": 0,
    }


def test_alert_summary_is_flagged_as_mock() -> None:
    detector = make_detector(n_min=1)
    low = {**EXPECTED, "P1": 49.5}

    (alert,) = [a for i in range(2) for a in detector.evaluate(low, EXPECTED, ts(i)).alerts]

    assert alert.summary is not None and alert.summary.startswith("[MOCK]")


def test_cusum_is_not_reset_by_an_alert_and_stops_at_the_cap() -> None:
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}

    cusum = [detector.evaluate(low, EXPECTED, ts(i)).residuals["P1"].cusum for i in range(6)]

    # alert at the second window (S = 9 > h = 8); S keeps growing until c*h = 16
    assert cusum == pytest.approx([4.5, 9.0, 13.5, 16.0, 16.0, 16.0])


def test_released_sensors_open_a_new_alert_on_a_new_drop() -> None:
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}
    # drop on windows 0-3 (S reaches the cap 16), normal on 4-28 (S = 16 - 0.5 j < h_close = 4
    # after 25 windows: released at window 28), drop again from window 29
    readings = [low if i < 4 or i >= 29 else EXPECTED for i in range(31)]

    opened = [i for i, obs in enumerate(readings) if detector.evaluate(obs, EXPECTED, ts(i)).alerts]

    assert opened == [1, 30]  # window 29: S = 3.5 + 4.5 = 8, not above h


def test_state_returns_to_normal_once_the_claimed_sensors_are_released() -> None:
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}
    readings = [low if i < 4 else EXPECTED for i in range(29)]  # released at window 28

    states = [detector.evaluate(obs, EXPECTED, ts(i)).state for i, obs in enumerate(readings)]

    assert states == ["normal"] + ["alarm"] * 27 + ["normal"]


def test_an_open_alert_does_not_hide_a_drop_on_other_sensors() -> None:
    detector = make_detector(n_min=2)
    first = {**EXPECTED, "P1": 49.5, "P2": 49.5}
    both = {**first, "P3": 49.5, "P4": 49.5}

    results = [detector.evaluate(first if i < 5 else both, EXPECTED, ts(i)) for i in range(8)]

    opened = {
        i: [d.sensor for d in a.deviating_sensors] for i, r in enumerate(results) for a in r.alerts
    }
    assert opened == {1: ["P1", "P2"], 6: ["P3", "P4"]}


def test_onset_is_the_last_window_with_zero_cusum() -> None:
    detector = make_detector(n_min=2)
    # P1 drops from window 3, P2 from window 4: both above h at window 5
    readings = [
        {**EXPECTED, "P1": 49.5 if i >= 3 else 50.0, "P2": 49.5 if i >= 4 else 50.0}
        for i in range(6)
    ]

    (alert,) = [
        a for i, obs in enumerate(readings) for a in detector.evaluate(obs, EXPECTED, ts(i)).alerts
    ]

    assert {d.sensor: d.since for d in alert.deviating_sensors} == {"P1": ts(2), "P2": ts(3)}
    assert alert.suspected_onset == ts(2)


def test_new_episode_keeps_the_last_zero_when_cusum_did_not_return_to_zero() -> None:
    # ADR-007 §6 taken literally (detection owner, 2026-10-17): since = last window with S = 0.
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}
    # drop on 3-6 (alert at 4, S = 16), normal on 7-31 (released at 31, S = 3.5 > 0),
    # drop again from 32 (alert at 33): S never went back to 0 after window 2
    readings = [low if 3 <= i <= 6 or i >= 32 else EXPECTED for i in range(34)]

    alerts = [
        (i, a)
        for i, obs in enumerate(readings)
        for a in detector.evaluate(obs, EXPECTED, ts(i)).alerts
    ]

    assert [(i, a.suspected_onset) for i, a in alerts] == [(4, ts(2)), (33, ts(2))]


def test_onset_is_unknown_when_a_sensor_was_never_at_zero() -> None:
    detector = make_detector(n_min=2)
    # P1 low from the first window evaluated (its S was never 0), P2 low from window 2
    readings = [{**EXPECTED, "P1": 49.5, "P2": 49.5 if i >= 2 else 50.0} for i in range(4)]

    (alert,) = [
        a for i, obs in enumerate(readings) for a in detector.evaluate(obs, EXPECTED, ts(i)).alerts
    ]

    assert {d.sensor: d.since for d in alert.deviating_sensors} == {"P1": None, "P2": ts(1)}
    assert alert.suspected_onset is None  # P1 started before anything the detector saw


# --------------------------------------------------------------------------- BE-012c


@pytest.mark.parametrize("resent", [1, 0], ids=["same-timestamp", "older-timestamp"])
def test_timestamp_not_after_the_last_is_rejected_without_changing_state(resent: int) -> None:
    detector = make_detector()
    low = {**EXPECTED, "P1": 49.5}
    detector.evaluate(low, EXPECTED, ts(0))
    detector.evaluate(low, EXPECTED, ts(1))  # S(P1) = 9.0

    with pytest.raises(DetectionError):
        detector.evaluate(low, EXPECTED, ts(resent))

    assert detector.evaluate(low, EXPECTED, ts(2)).residuals["P1"].cusum == pytest.approx(13.5)


LOW_P1 = {**EXPECTED, "P1": 49.5}


def assert_rejected_without_side_effects(
    observed: Mapping[str, float],
    expected: Mapping[str, float],
    at: datetime | None = None,
    make: Callable[[], CusumDetector] = make_detector,
    passing: Mapping[str, float] = LOW_P1,
) -> None:
    """The window rejected at ``at`` (default: window 1) leaves no trace: afterwards the
    detector matches a twin that only ever saw the windows that passed (``passing``)."""
    detector, twin = make(), make()
    for d in (detector, twin):
        d.evaluate(passing, EXPECTED, ts(0))

    with pytest.raises(DetectionError):
        detector.evaluate(observed, expected, at or ts(1))

    assert detector.evaluate(passing, EXPECTED, ts(2)) == twin.evaluate(passing, EXPECTED, ts(2))


WITHOUT_P4 = {s: v for s, v in EXPECTED.items() if s != "P4"}
WITH_P9 = {**EXPECTED, "P9": 50.0}


@pytest.mark.parametrize(
    ("observed", "expected"),
    [
        pytest.param(WITHOUT_P4, EXPECTED, id="observed-missing-sensor"),
        pytest.param(EXPECTED, WITHOUT_P4, id="expected-missing-sensor"),
        pytest.param(WITH_P9, EXPECTED, id="observed-unknown-sensor"),
        pytest.param(EXPECTED, WITH_P9, id="expected-unknown-sensor"),
    ],
)
def test_sensor_set_other_than_the_calibration_is_rejected(
    observed: dict[str, float], expected: dict[str, float]
) -> None:
    assert_rejected_without_side_effects(observed, expected)


@pytest.mark.parametrize(
    ("observed", "expected"),
    [
        pytest.param({**EXPECTED, "P4": float("nan")}, EXPECTED, id="observed-nan"),
        pytest.param({**EXPECTED, "P4": float("inf")}, EXPECTED, id="observed-inf"),
        pytest.param(EXPECTED, {**EXPECTED, "P4": float("nan")}, id="expected-nan"),
        pytest.param(EXPECTED, {**EXPECTED, "P4": float("-inf")}, id="expected-minus-inf"),
    ],
)
def test_non_finite_value_is_rejected(
    observed: dict[str, float], expected: dict[str, float]
) -> None:
    assert_rejected_without_side_effects(observed, expected)


def test_non_finite_standardised_residual_is_rejected() -> None:
    def tiny_sigma() -> CusumDetector:
        artefact = CalibrationArtefact(
            provenance="synthetic",
            sigma_m=dict.fromkeys(SENSORS, 5e-324),
            k=0.5,
            h=8.0,
            n_min=1,
            c=2.0,
            h_close=4.0,
        )
        return CusumDetector(artefact, interval=INTERVAL)

    # z = -0.5 / 5e-324 = -inf (a valid but tiny sigma)
    assert_rejected_without_side_effects(LOW_P1, EXPECTED, make=tiny_sigma, passing=EXPECTED)


def test_each_missing_window_lowers_cusum_by_k() -> None:
    detector = make_detector()
    low = {**EXPECTED, "P1": 49.5}
    detector.evaluate(low, EXPECTED, ts(0))
    detector.evaluate(low, EXPECTED, ts(1))  # S = 9.0

    after_gap = detector.evaluate(EXPECTED, EXPECTED, ts(5))  # windows 2-4 missing

    # 9 - 3 * 0.5 for the missing windows, then - 0.5 for the current (zero) residual
    assert after_gap.residuals["P1"].cusum == pytest.approx(7.0)


def test_missing_windows_are_reported_in_diagnostics() -> None:
    detector = make_detector()
    detector.evaluate(EXPECTED, EXPECTED, ts(0))

    after_gap = detector.evaluate(EXPECTED, EXPECTED, ts(4))  # windows 1-3 missing

    assert after_gap.diagnostics["skipped_windows"] == 3


def test_gap_that_empties_every_cusum_is_reported_as_reset() -> None:
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}
    detector.evaluate(low, EXPECTED, ts(0))
    detector.evaluate(low, EXPECTED, ts(1))  # S = 9 on P1, P2: alert, both claimed

    short = make_detector(n_min=2)
    for i in (0, 1):
        short.evaluate(low, EXPECTED, ts(i))

    emptied = detector.evaluate(EXPECTED, EXPECTED, ts(20))  # 18 missing: 9 - 18 * 0.5 = 0
    lowered = short.evaluate(EXPECTED, EXPECTED, ts(5))  # 3 missing: S = 7.5 before window 5

    assert (emptied.diagnostics.get("reset_reason"), emptied.state) == ("gap", "normal")
    assert "reset_reason" not in lowered.diagnostics


def test_onset_after_a_gap_that_emptied_cusum_is_inside_the_gap() -> None:
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}
    detector.evaluate(low, EXPECTED, ts(0))
    detector.evaluate(low, EXPECTED, ts(1))  # S = 9, never 0 so far

    detector.evaluate(low, EXPECTED, ts(30))  # 28 missing windows empty S; S = 4.5
    (alert,) = detector.evaluate(low, EXPECTED, ts(31)).alerts  # S = 9 > h

    assert alert.suspected_onset == ts(29)  # last missing window, where S was 0


class ExplodingMapping(Mapping[str, float]):
    """Raises on the n-th value lookup: a fault injected at any read of the input."""

    def __init__(self, data: dict[str, float], fail_at: int) -> None:
        self._data, self._fail_at, self._reads = data, fail_at, 0

    def __getitem__(self, key: str) -> float:
        self._reads += 1
        if self._reads == self._fail_at:
            raise RuntimeError("injected fault")
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


# 21 windows: P1, P2 low on 2-8, windows 10-12 missing, P3, P4 low from 15.
SCRIPT = [
    (
        i,
        {
            **EXPECTED,
            **dict.fromkeys(("P1", "P2"), 49.5 if 2 <= i <= 8 else 50.0),
            **dict.fromkeys(("P3", "P4"), 49.5 if i >= 15 else 50.0),
        },
    )
    for i in range(24)
    if i not in (10, 11, 12)
]

# An injector evaluates window ``index`` (previous window ``prev``, or None for the first one)
# with a fault; it raises when the fault fires.
Injector = Callable[[CusumDetector, int, int | None, Mapping[str, float]], DetectionResult]


def bad_input(
    make: Callable[
        [int, int | None, Mapping[str, float]], tuple[Mapping[str, float], Mapping[str, float], int]
    ],
) -> Injector:
    def inject(
        detector: CusumDetector, index: int, prev: int | None, obs: Mapping[str, float]
    ) -> DetectionResult:
        faulty_obs, faulty_exp, at = make(index, prev, obs)
        return detector.evaluate(faulty_obs, faulty_exp, ts(at))

    return inject


def exploding_read(side: Literal["observed", "expected"], n: int) -> Injector:
    return bad_input(
        lambda index, prev, obs: (
            (ExplodingMapping(dict(obs), n), EXPECTED, index)
            if side == "observed"
            else (obs, ExplodingMapping(EXPECTED, n), index)
        )
    )


def failing_contract_type(name: str) -> Injector:
    """Fault after the input was read: building ``name`` raises (for any exception, §9)."""

    def inject(
        detector: CusumDetector, index: int, prev: int | None, obs: Mapping[str, float]
    ) -> DetectionResult:
        def explode(*args: object, **kwargs: object) -> NoReturn:
            raise RuntimeError(f"injected while building {name}")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(f"sentinel.detection.cusum.{name}", explode)
            return detector.evaluate(obs, EXPECTED, ts(index))

    return inject


INJECTORS: dict[str, Injector] = {
    "missing-sensor": bad_input(lambda index, prev, obs: (WITHOUT_P4, EXPECTED, index)),
    "unknown-sensor": bad_input(lambda index, prev, obs: (obs, WITH_P9, index)),
    "non-finite": bad_input(
        lambda index, prev, obs: ({**obs, "P4": float("nan")}, EXPECTED, index)
    ),
    "naive-timestamp": lambda detector, index, prev, obs: detector.evaluate(
        obs, EXPECTED, ts(index).replace(tzinfo=None)
    ),
    # the first window has no previous one: it then passes and both runs see it
    "repeated-timestamp": bad_input(
        lambda index, prev, obs: (obs, EXPECTED, index if prev is None else prev)
    ),
    **{
        f"exploding-{side}-read-{n}": exploding_read(side, n)
        for side in ("observed", "expected")
        for n in range(1, 9)
    },
    **{
        f"failing-{name}": failing_contract_type(name)
        for name in (
            "SensorResidual",
            "DeviatingSensor",
            "CusumEvidence",
            "AlertCandidate",
            "DetectionResult",
        )
    },
}


def run_with_faults(inject: Injector, failed: set[int]) -> int:
    """Replay SCRIPT with ``inject`` at the positions in ``failed``. A window whose fault fires
    is lost; the twin never sees it. Returns how many faults fired."""
    detector, twin = make_detector(), make_detector()
    got, want, fired = [], [], 0
    for pos, (index, obs) in enumerate(SCRIPT):
        if pos in failed:
            try:
                got.append(inject(detector, index, SCRIPT[pos - 1][0] if pos else None, obs))
            except (DetectionError, RuntimeError):
                fired += 1
                continue
        else:
            got.append(detector.evaluate(obs, EXPECTED, ts(index)))
        want.append(twin.evaluate(obs, EXPECTED, ts(index)))
    assert got == want, f"fault at positions {sorted(failed)}"
    return fired


@pytest.mark.parametrize("fault", INJECTORS)
def test_run_with_failed_windows_equals_the_run_fed_only_the_passed_ones(fault: str) -> None:
    alone = sum(run_with_faults(INJECTORS[fault], {pos}) for pos in range(len(SCRIPT)))
    every_third = run_with_faults(INJECTORS[fault], set(range(0, len(SCRIPT), 3)))

    assert alone > 0 and every_third > 0  # the fault really fired


@pytest.mark.parametrize(
    "before_gap", [50.0, 49.94], ids=["S-zero-before-gap", "S-positive-before-gap"]
)
def test_onset_after_a_gap_does_not_depend_on_cusum_before_it(before_gap: float) -> None:
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}
    detector.evaluate({**EXPECTED, "P1": before_gap, "P2": before_gap}, EXPECTED, ts(1))

    detector.evaluate(low, EXPECTED, ts(30))  # windows 2-29 missing: S was 0 at window 29
    (alert,) = detector.evaluate(low, EXPECTED, ts(31)).alerts

    assert alert.suspected_onset == ts(29)


def test_naive_timestamp_is_rejected() -> None:
    assert_rejected_without_side_effects(EXPECTED, EXPECTED, at=ts(1).replace(tzinfo=None))


def test_gap_that_brings_cusum_below_h_close_releases_the_claimed_sensors() -> None:
    detector = make_detector(n_min=2)
    low = {**EXPECTED, "P1": 49.5, "P2": 49.5}
    for i in range(4):
        detector.evaluate(low, EXPECTED, ts(i))  # alert at 1, S = 16 at 3

    # windows 4-28 missing: S = 16 - 25 * 0.5 = 3.5 < h_close inside the gap, 8.0 after window 29
    detector.evaluate(low, EXPECTED, ts(29))
    reopened = detector.evaluate(low, EXPECTED, ts(30))  # S = 12.5 > h on released sensors

    assert [d.sensor for a in reopened.alerts for d in a.deviating_sensors] == ["P1", "P2"]
