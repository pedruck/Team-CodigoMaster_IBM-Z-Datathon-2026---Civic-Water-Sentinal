"""Determinism reference of the CUSUM detector (ADR-007 §9; BE-015 parity x86 / s390x).

A fixed replay goes straight into ``CusumDetector.evaluate`` (no HTTP, no region mapping) and
the sha256 of the JSON of every ``DetectionResult`` is compared with a literal recorded below.
The inputs use arithmetic only (``random.Random(seed).random()``, a triangular wave, steps,
one gap): no ``math.sin``, ``exp`` or ``gauss``, whose last bit depends on the C library. The
detector itself only adds, subtracts, multiplies, divides and compares float64, which IEEE 754
rounds the same everywhere, so the digest must be the same on every machine.

Everything here is invented (sensors, sigma, parameters, steps); it exercises the mechanics
and says nothing about leak detection.

This is the reference that BE-015 reproduces on the s390x target: run this test there. When
the detector's behaviour changes on purpose, regenerate the literal with
``uv run python -m tests.detection.test_determinism`` and say why in the commit. A change
that is not on purpose, or a different digest on another architecture, is a bug.
"""

import hashlib
import random

from sentinel.detection.cusum import CusumDetector
from sentinel.schemas.detections import DetectionResult
from tests.conftest import INTERVAL, ts
from tests.detection.artefacts import make_artefact

# Sigma are powers of two so the standardised residuals are exact; the noise is not.
SIGMA = {"P1": 0.125, "P2": 0.25, "P3": 0.25, "P4": 0.5}
GROUP_A, GROUP_B = ("P1", "P2"), ("P3", "P4")
DROP_M = 1.0
NOISE_M = 0.25  # peak to peak
SEED = 20260517

# A is low in windows 40-99 (its S sits at the cap c*h = 16, then decays after the step ends and
# the sensors are released); B is low from 130 on. Windows 70-79 never arrive (a short gap while
# A is claimed: S decays but the episode stays open); windows 170-229 never arrive either (a
# long gap while B is low: S is emptied, the sensors are released and B alerts again).
MISSING = frozenset(range(70, 80)) | frozenset(range(170, 230))
WINDOWS = [i for i in range(260) if i not in MISSING]

# BE-015 reference: sha256 of the replay below (one JSON per window, joined with "\n"). Recorded
# on x86-64; it must be the same on s390x. Regenerated on purpose in BE-012i: the alert gained the
# field ``candidate_pipe`` (null here: no network), which changes the JSON and nothing else. The
# previous reference, d9106c15e4d511effe3d15dfbca9a4ce83b4dc14d89f351de962f2e9cc72c413, is this
# replay serialised without that field (checked), so the numbers of the detector are unchanged.
REFERENCE_SHA256 = "7c5ef97bf8ccee4e19695475142658b426be29f796298314ab054a277ad3eee3"


def replay() -> list[DetectionResult]:
    rng = random.Random(SEED)
    detector = CusumDetector(make_artefact(SIGMA, n_min=2), interval=INTERVAL)
    results = []
    for i in WINDOWS:
        # triangular wave 0 .. 3 m with a period of 24 windows, a different level per sensor
        wave = 0.25 * abs(i % 24 - 12)
        expected = {s: 50.0 + 10.0 * n + wave for n, s in enumerate(SIGMA)}
        low = (GROUP_A if 40 <= i < 100 else ()) + (GROUP_B if i >= 130 else ())
        observed = {
            s: expected[s] + (rng.random() - 0.5) * NOISE_M - (DROP_M if s in low else 0.0)
            for s in SIGMA
        }
        results.append(detector.evaluate(observed, expected, ts(i)))
    return results


def digest(results: list[DetectionResult]) -> str:
    return hashlib.sha256("\n".join(r.model_dump_json() for r in results).encode()).hexdigest()


def test_the_replay_covers_alerts_release_and_both_gaps() -> None:
    results = replay()

    opened = [r.timestamp for r in results if r.alerts]
    states = [r.state for r in results]
    assert len(opened) == 3  # A, B, and B again after the long gap
    assert "normal" in states[states.index("alarm") :]  # a release between the episodes
    skipped = {r.diagnostics["skipped_windows"] for r in results}
    assert {10, 60} <= skipped
    assert [r.diagnostics.get("reset_reason") for r in results].count("gap") == 1


def test_replay_digest_is_the_reference_of_be_015() -> None:
    assert digest(replay()) == REFERENCE_SHA256


if __name__ == "__main__":
    print(digest(replay()))
