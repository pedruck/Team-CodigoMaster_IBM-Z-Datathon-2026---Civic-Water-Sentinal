"""Synthetic residuals for the calibration tests (ADR-008 §E).

Invented signal, harder than the real one is expected to be: AR(1) noise (autocorrelated at
5 minutes), a diurnal swing of the noise scale (heteroscedasticity) and a component common to
every sensor. Its scales are made up; nothing here comes from data or says what a real
residual looks like.

Sensor ``i`` at window ``t`` (a 24 h cycle is ``WINDOWS_PER_DAY`` windows)::

    r[i, t] = scale[i] * (1 + amplitude * sin(2 pi t / WINDOWS_PER_DAY)) * e[i, t]
              + common_scale * u[t]

``e[i, ·]`` and ``u`` are unit-variance AR(1) processes, ``e`` independent per sensor and ``u``
shared. At a fixed window ``t`` the residual is normal with a known standard deviation, so the
robust scale of the whole series can be computed without looking at the sample.
"""

import math
import random
from collections.abc import Mapping

WINDOWS_PER_DAY = 288
MAD_TO_SIGMA = 1.4826  # 1 / Phi^-1(3/4): turns a normal MAD into a standard deviation


def ar1(rng: random.Random, windows: int, phi: float) -> list[float]:
    """Stationary AR(1) with unit variance: ``x[t] = phi x[t-1] + sqrt(1 - phi^2) eps[t]``."""
    drive = math.sqrt(1.0 - phi * phi)
    x = rng.gauss(0.0, 1.0)
    out = []
    for _ in range(windows):
        out.append(x)
        x = phi * x + drive * rng.gauss(0.0, 1.0)
    return out


def swing(t: int, amplitude: float) -> float:
    return 1.0 + amplitude * math.sin(2.0 * math.pi * t / WINDOWS_PER_DAY)


def synthetic_residuals(
    scales: Mapping[str, float],
    *,
    windows: int,
    seed: int,
    phi: float,
    amplitude: float,
    common_scale: float,
) -> dict[str, list[float]]:
    """``windows`` should be a whole number of days so every phase of the swing appears equally."""
    rng = random.Random(seed)
    common = ar1(rng, windows, phi)
    series = {}
    for sensor, scale in scales.items():
        own = ar1(rng, windows, phi)
        series[sensor] = [
            scale * swing(t, amplitude) * own[t] + common_scale * common[t] for t in range(windows)
        ]
    return series


def robust_scale(scale: float, *, amplitude: float, common_scale: float) -> float:
    """``MAD_TO_SIGMA`` x the population MAD of the generator above, found by bisection.

    The series is a mixture of zero-mean normals, one per phase of the day, so
    ``P(|r| <= q) = mean over phases of erf(q / (sd_phase sqrt 2))`` and the MAD is the ``q``
    where that equals one half. Independent of the estimator under test.
    """
    sds = [math.hypot(scale * swing(t, amplitude), common_scale) for t in range(WINDOWS_PER_DAY)]

    def within(q: float) -> float:
        return sum(math.erf(q / (sd * math.sqrt(2.0))) for sd in sds) / len(sds)

    low, high = 0.0, 10.0 * max(sds)
    for _ in range(200):
        mid = (low + high) / 2.0
        low, high = (mid, high) if within(mid) < 0.5 else (low, mid)
    return MAD_TO_SIGMA * (low + high) / 2.0
