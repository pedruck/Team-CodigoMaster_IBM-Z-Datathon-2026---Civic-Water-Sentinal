"""CUSUM detector through the ``Detector`` port (contract §3, ADR-007; BE-012a slice).

The calibration numbers below are invented for the tests; they are not results.
"""

import pytest

from sentinel.detection.calibration import CalibrationArtefact, Provenance
from sentinel.detection.cusum import CusumDetector
from sentinel.schemas.detections import CusumEvidence, SensorResidual
from tests.conftest import ts

SENSORS = ("P1", "P2", "P3", "P4")
EXPECTED = dict.fromkeys(SENSORS, 50.0)


def make_detector(n_min: int = 2) -> CusumDetector:
    return CusumDetector(
        CalibrationArtefact(
            provenance="synthetic",
            sigma_m=dict.fromkeys(SENSORS, 0.1),
            k=0.5,
            h=8.0,
            n_min=n_min,
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
def test_simplified_detector_is_always_labelled_mock(provenance: Provenance) -> None:
    detector = CusumDetector(
        CalibrationArtefact(provenance=provenance, sigma_m={"P1": 0.1}, k=0.5, h=8.0, n_min=1)
    )

    result = detector.evaluate({"P1": 50.0}, {"P1": 50.0}, ts(0))

    assert detector.info.is_mock and result.model.is_mock


def test_anomaly_score_is_nth_largest_cusum() -> None:
    detector = make_detector(n_min=2)
    # S after one window: P1 = 5 - 0.5 = 4.5, P2 = 3 - 0.5 = 2.5, P3 = P4 = 0
    result = detector.evaluate({**EXPECTED, "P1": 49.5, "P2": 49.7}, EXPECTED, ts(0))

    assert result.anomaly_score == pytest.approx(2.5)


def test_diagnostics_report_the_calibration_in_use() -> None:
    result = make_detector(n_min=2).evaluate(EXPECTED, EXPECTED, ts(0))

    assert result.diagnostics == {"k": 0.5, "h": 8.0, "n_min": 2, "provenance": "synthetic"}


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
