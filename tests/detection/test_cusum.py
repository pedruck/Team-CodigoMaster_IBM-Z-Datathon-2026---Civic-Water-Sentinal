"""CUSUM detector through the ``Detector`` port (contract §3, ADR-007; BE-012a/b slices).

The calibration numbers below are invented for the tests; they are not results.
"""

import pytest

from sentinel.detection.calibration import CalibrationArtefact, Provenance
from sentinel.detection.cusum import CusumDetector
from sentinel.schemas.detections import CusumEvidence, SensorResidual
from tests.conftest import ts

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
        )
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
    }


def test_alert_summary_is_flagged_as_mock() -> None:
    detector = make_detector(n_min=1)
    low = {**EXPECTED, "P1": 49.5}

    (alert,) = [a for i in range(2) for a in detector.evaluate(low, EXPECTED, ts(i)).alerts]

    assert alert.summary is not None and alert.summary.startswith("[MOCK]")


def test_failed_evaluation_leaves_the_state_unchanged() -> None:
    detector = make_detector()
    low = {**EXPECTED, "P1": 49.5}
    detector.evaluate(low, EXPECTED, ts(0))  # S(P1) = 4.5

    # Non-finite input fails while the result is built (BE-012c turns it into DetectionError).
    with pytest.raises(ValueError):
        detector.evaluate(low, {**EXPECTED, "P4": float("nan")}, ts(1))
    after = detector.evaluate(low, EXPECTED, ts(2))

    assert after.residuals["P1"].cusum == pytest.approx(9.0)


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
