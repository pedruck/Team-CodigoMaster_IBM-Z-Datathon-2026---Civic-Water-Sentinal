"""Property tests of the CUSUM detector through ``CusumDetector.evaluate`` (ADR-008 §E, BE-012d).

They check how the detector behaves as a function of its input (a larger step is detected
earlier, scaling the data and sigma together changes nothing, a null series false-alarms at the
theoretical rate), never that a particular ``h`` gives a particular result on data.

Every number below is invented for the tests: the sigmas, ``k``, ``h``, ``n_min``, ``c``,
``h_close``, the step sizes, the seeds and the noise. The noise comes from ``random.Random`` with
a fixed seed, so the runs are deterministic; it is used here only as a statistical test input.
The sensors ("P1", "P2", ...) are placeholders, not real network elements.
"""

import math
import random
from collections.abc import Sequence
from typing import Any

import pytest

from sentinel.detection.cusum import CusumDetector
from sentinel.schemas.detections import DetectionResult
from tests.conftest import INTERVAL, ts
from tests.detection.artefacts import make_artefact

SEEDS = (0, 1, 2)


# --- 1. A larger step is detected earlier -------------------------------------------------

STEP_SIGMAS = (0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0)  # steps in units of sigma, increasing
STEP_SENSORS = ("P1", "P2")
STEP_ONSET = 20  # first window with the step (the windows before are noise only)
STEP_WINDOWS = 150


def first_alert_index(step_sigmas: float, noise: Sequence[Sequence[float]]) -> int:
    """Index of the first window with an alert, or ``STEP_WINDOWS`` if there is none.

    Each window is ``expected - (step + noise) * sigma`` on every sensor, so ``z`` is the step
    (in sigma) plus the noise: for one noise sequence, ``z`` is larger everywhere for a larger
    step, and S is monotone in ``z``."""
    sigma = 0.1
    artefact = make_artefact(dict.fromkeys(STEP_SENSORS, sigma), n_min=len(STEP_SENSORS))
    detector = CusumDetector(artefact, interval=INTERVAL)
    expected = dict.fromkeys(STEP_SENSORS, 50.0)
    for i in range(STEP_WINDOWS):
        step = step_sigmas if i >= STEP_ONSET else 0.0
        observed = {s: 50.0 - (step + noise[i][j]) * sigma for j, s in enumerate(STEP_SENSORS)}
        if detector.evaluate(observed, expected, ts(i)).alerts:
            return i
    return STEP_WINDOWS


@pytest.mark.parametrize("seed", SEEDS)
def test_larger_step_is_detected_no_later(seed: int) -> None:
    rng = random.Random(seed)
    noise = [[rng.gauss(0, 1) for _ in STEP_SENSORS] for _ in range(STEP_WINDOWS)]

    first = [first_alert_index(step, noise) for step in STEP_SIGMAS]

    assert first == sorted(first, reverse=True), f"first alert per step {STEP_SIGMAS}: {first}"
    assert first[-1] < STEP_WINDOWS, "the largest step must raise an alert"
    assert first[-1] < first[0], "the property would hold trivially if every step tied"


# --- 2. Scale invariance ------------------------------------------------------------------

SCALE_SENSORS = ("P1", "P2", "P3")
SCALE_SIGMAS = (0.1, 0.2, 0.05)
SCALE_WINDOWS = 100
SCALE_GAP = range(50, 55)  # windows never sent: exercises the decay across a gap
SCALE_DROPS = (range(20, 45), range(70, 80))  # windows with a pressure drop of 3 sigma


def scale_run(lam: float, seed: int) -> list[DetectionResult]:
    """One scenario (drop, release, gap, second drop; ``expected = 0``) with the readings and
    every sigma multiplied by ``lam``. The noise is drawn in units of sigma, so the multiplied
    readings are the base readings times ``lam`` exactly, whatever ``lam`` is."""
    rng = random.Random(seed)
    artefact = make_artefact(
        {s: sigma * lam for s, sigma in zip(SCALE_SENSORS, SCALE_SIGMAS, strict=True)},
        n_min=2,
    )
    detector = CusumDetector(artefact, interval=INTERVAL)
    expected = dict.fromkeys(SCALE_SENSORS, 0.0)
    results = []
    for i in range(SCALE_WINDOWS):
        drop = 3.0 if any(i in r for r in SCALE_DROPS) else 0.0
        units = {s: rng.gauss(0, 1) - drop for s in SCALE_SENSORS}  # observed / sigma (base)
        if i in SCALE_GAP:
            continue
        observed = {
            s: units[s] * sigma * lam for s, sigma in zip(SCALE_SENSORS, SCALE_SIGMAS, strict=True)
        }
        results.append(detector.evaluate(observed, expected, ts(i)))
    return results


def comparable(result: DetectionResult, lam: float) -> dict[str, Any]:
    """The result as a dict without what is expected to change with the scale.

    ``model.version`` carries the hash of the artefact, which holds sigma, and
    ``diagnostics["calibration_id"]`` is that same hash: both change when sigma does, by design
    (ADR-008 §B), so they are dropped. ``residual_m`` and ``deviation_m`` are in metres and
    scale with the data: they are multiplied by ``lam`` (exact for a power of two)."""
    dump = result.model_dump()
    del dump["model"]
    del dump["diagnostics"]["calibration_id"]
    for residual in dump["residuals"].values():
        residual["residual_m"] *= lam
    for alert in dump["alerts"]:
        for sensor in alert["deviating_sensors"]:
            sensor["deviation_m"] *= lam
    return dump


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("lam", [0.25, 4.0, 1024.0])
def test_scaling_data_and_sigma_together_changes_nothing(lam: float, seed: int) -> None:
    base = scale_run(1.0, seed)
    scaled = scale_run(lam, seed)

    # The scenario must exercise the interesting paths, or equality would prove little.
    assert any(r.alerts for r in base)
    assert {"normal", "alarm"} <= {r.state for r in base}
    assert any(r.diagnostics["skipped_windows"] for r in base)
    # The exclusion in ``comparable`` is real: the artefact (and so the model version) differs.
    assert base[0].model.version != scaled[0].model.version

    assert [comparable(r, lam) for r in base] == [comparable(r, 1.0) for r in scaled]


# --- 3. Null false-alarm rate matches the theoretical ARL0 ---------------------------------

NULL_K = 0.5
NULL_H = 3.0
NULL_WINDOWS = 100_000
NULL_TOLERANCE = 0.15


def siegmund_arl0(k: float, h: float) -> float:
    """Siegmund's approximation of the in-control average run length of a one-sided CUSUM on
    i.i.d. N(0, 1) with reference value ``k`` and limit ``h``: ``b = h + 1.166`` corrects the
    overshoot of a discrete process over the limit of the continuous one."""
    b = h + 1.166
    return (math.exp(2 * k * b) - 2 * k * b - 1) / (2 * k * k)


def run_lengths(windows: int, seed: int) -> list[int]:
    """Windows from each start (S = 0) to the next alert opening, on i.i.d. N(0, 1) ``z``.

    The formula restarts the CUSUM at 0 after each alarm; the detector does not reset, it keeps
    accumulating while the sensor is claimed and releases it once S < ``h_close``. With a
    minuscule ``h_close`` that is when S falls back to 0, so a run is counted from the
    release window (S = 0, the formula's start) to the next opening; the excursion between an
    opening and its release is not part of a run. By the strong Markov property the runs are
    i.i.d. The first one starts before the first window (S = 0 initially)."""
    artefact = make_artefact(
        {"P1": 1.0}, k=NULL_K, h=NULL_H, n_min=1, c=1e6, h_close=1e-9
    )  # cap c*h is out of reach
    detector = CusumDetector(artefact, interval=INTERVAL)
    rng = random.Random(seed)
    lengths: list[int] = []
    start, in_alarm = -1, False
    for i in range(windows):
        z = rng.gauss(0, 1)  # z = (expected - observed) / sigma = z with sigma 1
        result = detector.evaluate({"P1": -z}, {"P1": 0.0}, ts(i))
        if in_alarm:
            if result.state != "alarm":  # released: S < h_close, a new run starts here
                in_alarm, start = False, i
        elif result.alerts:
            lengths.append(i - start)
            in_alarm = True
    return lengths


def test_null_series_run_length_matches_siegmund_arl0() -> None:
    arl0 = siegmund_arl0(NULL_K, NULL_H)
    assert arl0 == pytest.approx(118.6, abs=0.1)  # the formula is transcribed correctly

    lengths = run_lengths(NULL_WINDOWS, seed=0)

    assert len(lengths) >= 500  # about 100000 / (ARL0 + excursion): enough runs for the mean
    mean = sum(lengths) / len(lengths)
    assert mean == pytest.approx(arl0, rel=NULL_TOLERANCE), (
        f"mean run length {mean:.1f} over {len(lengths)} runs vs ARL0 {arl0:.1f}"
    )
