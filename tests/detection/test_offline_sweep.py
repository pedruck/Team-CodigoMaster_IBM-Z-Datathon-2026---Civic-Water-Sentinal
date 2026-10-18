"""Fast CUSUM replay and parameter sweep (BE-012f, ADR-008 §C/§E).

Everything here is synthetic: the ``z`` matrices are made up for the tests (hand-written steps
and seeded ``numpy`` noise), as are the parameters, the labelled leaks and the sensor names.
No number is a result and none comes from data. The hand-written ``z`` are multiples of 0.5, so
the hand-computed expectations are exact in float64.

``replay`` is tested against its two sources of truth: expectations worked out by hand from
ADR-007, and the ``CusumDetector`` itself, which it must match bit for bit.
"""

import inspect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from sentinel.detection.cusum import CusumDetector
from sentinel.detection.offline.layer1 import MatchRule
from sentinel.detection.offline.sweep import Opening, replay, sweep, trajectory
from tests.conftest import INTERVAL, ts
from tests.detection.artefacts import make_artefact


def run(
    z: np.ndarray,
    *,
    k: float = 0.5,
    h: float = 8.0,
    n_min: int = 1,
    c: float = 2.0,
    h_close: float = 4.0,
) -> list[Opening]:
    """``replay`` over the windows ``ts(0), ts(1), ...`` with invented default parameters:
    a ``z`` of 4.5 adds 4.0 to S per window, the cap ``c*h`` is 16."""
    return replay(z, [ts(i) for i in range(len(z))], k=k, h=h, n_min=n_min, c=c, h_close=h_close)


def column(*values: float) -> np.ndarray:
    """A one-sensor ``z`` matrix (T x 1)."""
    return np.array(values, dtype=np.float64).reshape(-1, 1)


# --- 1. Hand-computed expectations ---------------------------------------------------------


def test_a_step_opens_when_s_first_exceeds_h_with_the_last_zero_as_onset() -> None:
    # S per window: 0, 0, 4, 8, 12. S == h at window 3 does not open (strictly above h).
    openings = run(column(0, 0, 4.5, 4.5, 4.5, 4.5))

    assert openings == [
        Opening(index=4, timestamp=ts(4), sensors=(0,), statistic=12.0, suspected_onset=ts(1))
    ]


def test_a_claimed_sensor_is_released_only_when_s_falls_below_h_close() -> None:
    # k = 0.5, h = 8, h_close = 4. S per window:
    #   w0: 0 | w1: 4 | w2: 8 (== h, no alert) | w3: 12 (alert, sensor claimed)
    #   w4..w19: -0.5 each, 11.5 ... 4.0 (== h_close: still claimed)
    #   w20: z = 8.5 gives 12.0 > h, but the sensor is still claimed: no alert
    #   w21..w37: -0.5 each, 11.5 ... 3.5 (< h_close at w37: released)
    #   w38: 7.5 | w39: 11.5 (new alert)
    z = column(*[0, 4.5, 4.5, 4.5], *[0] * 16, 8.5, *[0] * 17, 4.5, 4.5)

    openings = run(z)

    assert [(o.index, o.sensors, o.statistic) for o in openings] == [
        (3, (0,), 12.0),
        (39, (0,), 11.5),
    ]
    # S was 0 only at w0, so the onset of the second alert is w0, not the release.
    assert [o.suspected_onset for o in openings] == [ts(0), ts(0)]


def test_the_statistic_is_the_nth_largest_s_of_the_unclaimed_sensors() -> None:
    # N = 2, four sensors. Sensors 0 and 1 climb by 4 and 3 per window and are above h
    # together at w3 (S = 12 and 9): the alert has statistic 9, the 2nd largest, not 12.
    # Sensor 2 (+2 per window) is above h alone from w5 on: 1 unclaimed sensor, no alert.
    # Sensor 3 starts to climb at w6 (+4): at w8 sensors 2 and 3 are unclaimed above h
    # (S = 16 and 12), a second alert whose statistic is 12.
    z = np.zeros((9, 4))
    z[1:, 0] = 4.5
    z[1:, 1] = 3.5
    z[1:, 2] = 2.5
    z[6:, 3] = 4.5

    openings = run(z, n_min=2)

    assert [(o.index, o.sensors, o.statistic) for o in openings] == [
        (3, (0, 1), 9.0),
        (8, (2, 3), 12.0),
    ]
    # Onset: the earliest last-zero among the alert's sensors (w0 for 0, 1 and 2; w5 for 3).
    assert [o.suspected_onset for o in openings] == [ts(0), ts(0)]


def test_sensors_are_ranked_by_s_descending_and_ties_by_index() -> None:
    # w1: S = 9, 11, 11 on sensors 0, 1, 2: all cross h in the same window.
    z = np.zeros((2, 3))
    z[1] = [9.5, 11.5, 11.5]

    by_n = {n: run(z, n_min=n) for n in (1, 2, 3)}

    assert {n: [o.sensors for o in openings] for n, openings in by_n.items()} == {
        1: [(1, 2, 0)],
        2: [(1, 2, 0)],
        3: [(1, 2, 0)],
    }
    # statistic: the Nth value of the ranking 11, 11, 9.
    assert {n: [o.statistic for o in openings] for n, openings in by_n.items()} == {
        1: [11.0],
        2: [11.0],
        3: [9.0],
    }


def test_the_onset_is_unknown_if_any_sensor_of_the_alert_was_never_zero() -> None:
    # Sensor 0 is 0 at w0; sensor 1 is never 0 (it climbs from the first window).
    z = np.zeros((4, 2))
    z[1:, 0] = 4.5  # S: 0, 4, 8, 12
    z[:, 1] = 4.5  # S: 4, 8, 12, 16

    openings = run(z, n_min=2)

    assert [(o.index, o.sensors, o.statistic, o.suspected_onset) for o in openings] == [
        (3, (1, 0), 12.0, None)
    ]


def test_s_is_capped_at_c_times_h() -> None:
    openings = run(column(0, 100, 100), c=2.0, h=8.0)

    assert [(o.index, o.statistic) for o in openings] == [(1, 16.0)]


def test_no_alert_while_fewer_than_n_sensors_are_above_h() -> None:
    z = np.zeros((6, 3))
    z[1:, 0] = 4.5  # only sensor 0 ever crosses h

    assert run(z, n_min=2) == []


# --- 2. Parity with CusumDetector ----------------------------------------------------------


@dataclass(frozen=True)
class Params:
    """Invented detector parameters (names as in the calibration artefact)."""

    k: float
    h: float
    n_min: int
    c: float
    h_close: float


def detector_openings(z: np.ndarray, p: Params) -> list[Opening]:
    """The alerts ``CusumDetector.evaluate`` opens over ``z``, in the shape of ``Opening``.

    With sigma = 1 and ``expected = 0``, ``observed = -z`` gives the detector exactly ``z``."""
    sensors = [f"P{i:02d}" for i in range(z.shape[1])]
    artefact = make_artefact(
        dict.fromkeys(sensors, 1.0), k=p.k, h=p.h, n_min=p.n_min, c=p.c, h_close=p.h_close
    )
    detector = CusumDetector(artefact, interval=INTERVAL)
    expected = dict.fromkeys(sensors, 0.0)
    openings = []
    for t in range(len(z)):
        observed = {s: -float(z[t, j]) for j, s in enumerate(sensors)}
        for alert in detector.evaluate(observed, expected, ts(t)).alerts:
            assert alert.cusum is not None
            openings.append(
                Opening(
                    index=t,
                    timestamp=ts(t),
                    sensors=tuple(sensors.index(d.sensor) for d in alert.deviating_sensors),
                    statistic=alert.cusum.statistic,
                    suspected_onset=alert.suspected_onset,
                )
            )
    return openings


def replay_params(z: np.ndarray, p: Params) -> list[Opening]:
    return run(z, k=p.k, h=p.h, n_min=p.n_min, c=p.c, h_close=p.h_close)


PARITY_PARAMS = (
    Params(k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0),
    Params(k=0.5, h=8.0, n_min=3, c=2.0, h_close=4.0),
    Params(k=0.25, h=6.0, n_min=2, c=1.5, h_close=6.0),  # h_close == h
    Params(k=1.0, h=5.0, n_min=4, c=3.0, h_close=2.5),
)
PARITY_SENSORS = 8
PARITY_WINDOWS = 900


def parity_z(seed: int, *, quantised: bool) -> np.ndarray:
    """Noise plus drops (``z > 0``) injected on random groups of sensors: some start at window 0
    (their S is never 0 before the alert), some repeat on the same sensors (release, then a new
    alert), some overlap in time (concurrent alerts). With ``quantised`` every value is a
    multiple of 0.25, so S is exact and lands on h, h_close and on ties between sensors."""
    rng = np.random.default_rng(seed)
    z = rng.normal(0.0, 1.0, (PARITY_WINDOWS, PARITY_SENSORS))
    for episode in range(14):
        group = rng.choice(PARITY_SENSORS, size=int(rng.integers(1, 5)), replace=False)
        start = 0 if episode == 0 else int(rng.integers(0, PARITY_WINDOWS - 60))
        stop = start + int(rng.integers(15, 120))
        z[start:stop, group] += rng.uniform(1.0, 6.0)
    return np.round(z * 4.0) / 4.0 if quantised else z


PARITY_SEEDS = (0, 1, 2)


@pytest.mark.parametrize("quantised", [False, True])
@pytest.mark.parametrize("p", PARITY_PARAMS)
@pytest.mark.parametrize("seed", PARITY_SEEDS)
def test_replay_opens_exactly_the_alerts_of_the_detector(
    seed: int, p: Params, quantised: bool
) -> None:
    z = parity_z(seed, quantised=quantised)

    got = replay_params(z, p)

    # ``==``: same windows, same ranked sensors, the same float64 statistic, same onset.
    assert got == detector_openings(z, p)
    assert len(got) >= 3, "a scenario with almost no alert would prove little"


def test_the_parity_scenarios_cover_the_interesting_paths() -> None:
    alerts = [
        replay_params(parity_z(seed, quantised=quantised), p)
        for seed in PARITY_SEEDS
        for p in PARITY_PARAMS
        for quantised in (False, True)
    ]
    sensor_alerts = [[s for o in got for s in o.sensors] for got in alerts]

    assert any(len(o.sensors) > 1 for got in alerts for o in got)  # several sensors per alert
    assert any(len(s) > len(set(s)) for s in sensor_alerts)  # a sensor released, then claimed again
    assert any(o.suspected_onset is None for got in alerts for o in got)  # onset unknown
    assert any(o.suspected_onset is not None for got in alerts for o in got)


def test_replay_matches_the_detector_when_all_sensors_tie_at_the_cap() -> None:
    # Every sensor gets the same z: S is equal on all of them, then capped (16): the ranking
    # of the alert is decided by the index alone.
    z = np.zeros((40, 6))
    z[5:30, :] = 4.5
    p = Params(k=0.5, h=8.0, n_min=3, c=2.0, h_close=4.0)

    got = replay_params(z, p)

    assert got == detector_openings(z, p)
    assert [o.sensors for o in got] == [(0, 1, 2, 3, 4, 5)]


# --- 3. Input validation -------------------------------------------------------------------


def good_z() -> np.ndarray:
    return np.zeros((6, 3))


def test_a_pandas_index_is_accepted_and_the_timestamps_are_plain_datetimes() -> None:
    index = pd.date_range(ts(0), periods=6, freq="5min", tz=UTC)

    openings = replay(
        column(0, 0, 4.5, 4.5, 4.5, 4.5), index, k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0
    )

    assert [(o.timestamp, o.suspected_onset) for o in openings] == [(ts(4), ts(1))]
    assert type(openings[0].timestamp) is datetime
    assert type(openings[0].suspected_onset) is datetime


def test_an_empty_matrix_has_no_alerts() -> None:
    assert replay(np.zeros((0, 3)), [], k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0) == []


def test_an_irregular_time_grid_is_refused() -> None:
    times = [ts(0), ts(1), ts(2), ts(4), ts(5), ts(6)]  # window 3 is missing

    with pytest.raises(ValueError, match="regular"):
        replay(good_z(), times, k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0)


def test_an_irregular_pandas_index_is_refused() -> None:
    index = pd.DatetimeIndex([ts(i) for i in (0, 1, 2, 4, 5, 6)])

    with pytest.raises(ValueError, match="regular"):
        replay(good_z(), index, k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0)


@pytest.mark.parametrize(
    "times",
    [
        [ts(0), ts(0), ts(0), ts(0), ts(0), ts(0)],  # no step
        [ts(5), ts(4), ts(3), ts(2), ts(1), ts(0)],  # backwards
    ],
)
def test_a_grid_that_does_not_advance_is_refused(times: list[datetime]) -> None:
    with pytest.raises(ValueError, match="regular"):
        replay(good_z(), times, k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0)


def test_naive_and_aware_timestamps_cannot_be_mixed() -> None:
    times = [ts(i) for i in range(5)] + [datetime(2019, 1, 1, 0, 25)]

    with pytest.raises(ValueError, match="regular"):
        replay(good_z(), times, k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0)


def test_one_timestamp_per_row_of_z_is_required() -> None:
    with pytest.raises(ValueError, match="rows"):
        replay(good_z(), [ts(i) for i in range(5)], k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0)


@pytest.mark.parametrize(
    ("z", "match"),
    [
        (np.zeros(6), "2-D"),
        (np.zeros((6, 3), dtype=np.float32), "float64"),
        (np.zeros((6, 3), dtype=np.int64), "float64"),
        (np.full((6, 3), np.nan), "finite"),
        (np.full((6, 3), np.inf), "finite"),
        (np.zeros((6, 0)), "sensor"),
    ],
)
def test_z_must_be_a_finite_float64_matrix(z: np.ndarray, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        replay(z, [ts(i) for i in range(6)], k=0.5, h=8.0, n_min=1, c=2.0, h_close=4.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"k": 0.0},
        {"k": -0.5},
        {"k": math.nan},
        {"h": 0.0},
        {"h": math.inf},
        {"c": 1.0},  # the cap c*h must be above h
        {"c": math.nan},
        {"h_close": 0.0},
        {"h_close": 9.0},  # above h = 8: the artefact refuses it too
        {"n_min": 0},
        {"n_min": 4},  # more than the 3 sensors
    ],
)
def test_parameters_the_artefact_would_refuse_are_refused(overrides: dict[str, float]) -> None:
    params: dict[str, float] = {"k": 0.5, "h": 8.0, "n_min": 1, "c": 2.0, "h_close": 4.0}
    params.update(overrides)

    with pytest.raises(ValueError, match=next(iter(overrides))):
        replay(
            good_z(),
            [ts(i) for i in range(6)],
            k=params["k"],
            h=params["h"],
            n_min=int(params["n_min"]),
            c=params["c"],
            h_close=params["h_close"],
        )


# --- 4. The sweep --------------------------------------------------------------------------

# Invented tolerance: an abrupt leak must be seen within 20 windows, an incipient one within 100.
RULE = MatchRule(max_delay={"abrupt": 20 * INTERVAL, "incipient": 100 * INTERVAL})


def leaks_frame(
    *rows: tuple[str, str, int, int], peaks: Mapping[str, int] | None = None
) -> pd.DataFrame:
    """Invented labelled leaks ``(pipe, type, start window, end window)``; the peak is the
    start unless ``peaks`` gives a window for the pipe."""
    peak_of = {pipe: start for pipe, _, start, _ in rows} | dict(peaks or {})
    return pd.DataFrame(
        {
            "pipe": [pipe for pipe, _, _, _ in rows],
            "start": pd.to_datetime([ts(start) for _, _, start, _ in rows]),
            "end": pd.to_datetime([ts(end) for _, _, _, end in rows]),
            "leak_size": [0.1] * len(rows),
            "type": [kind for _, kind, _, _ in rows],
            "peak": pd.to_datetime([ts(peak_of[pipe]) for pipe, _, _, _ in rows]),
        }
    )


def run_sweep(
    z: np.ndarray,
    leaks: pd.DataFrame,
    *,
    h_values: Sequence[float],
    n_values: Sequence[int],
    k: float = 0.5,
    c: float = 2.0,
    h_close: float = 4.0,
) -> pd.DataFrame:
    return sweep(
        z,
        [ts(i) for i in range(len(z))],
        leaks,
        k=k,
        h_values=h_values,
        n_values=n_values,
        c=c,
        h_close=h_close,
        match=RULE,
    )


def row_of(table: pd.DataFrame, h: float, n_min: int) -> pd.Series:
    """The row of the configuration ``(h, n_min)``."""
    return table[(table["h"] == h) & (table["n_min"] == n_min)].iloc[0]


def two_steps() -> np.ndarray:
    """300 windows, two sensors: sensor 0 steps (z = 4.5) over windows 100..129, sensor 1 over
    200..229. A leak is labelled for the first step only, so the second one is a false alarm."""
    z = np.zeros((300, 2))
    z[100:130, 0] = 4.5
    z[200:230, 1] = 4.5
    return z


def test_the_sweep_has_one_row_per_h_and_n_in_the_order_given() -> None:
    table = run_sweep(two_steps(), leaks_frame(), h_values=[12.0, 8.0, 200.0], n_values=[2, 1])

    assert list(zip(table["h"], table["n_min"], strict=True)) == [
        (12.0, 2),
        (12.0, 1),
        (8.0, 2),
        (8.0, 1),
        (200.0, 2),
        (200.0, 1),
    ]


def test_the_sweep_is_deterministic() -> None:
    first = run_sweep(
        two_steps(), leaks_frame(("p1", "abrupt", 100, 250)), h_values=[8.0, 12.0], n_values=[1, 2]
    )
    second = run_sweep(
        two_steps(), leaks_frame(("p1", "abrupt", 100, 250)), h_values=[8.0, 12.0], n_values=[1, 2]
    )

    pd.testing.assert_frame_equal(first, second)


def test_each_row_holds_the_counts_delays_and_false_alarms_of_that_configuration() -> None:
    # Step on sensor 0 from w100: S = 4, 8, 12, 16 ..., so it is above h = 8 at w102 and above
    # h = 12 at w103. The leak p1 (abrupt, window w100..w120) is detected after 2 or 3 windows.
    # The step on sensor 1 from w200 is outside every window: one false alarm per configuration
    # with N = 1. With N = 2 the steps never overlap: no alert, p1 is missed. With h = 200
    # nothing crosses.
    table = run_sweep(
        two_steps(),
        leaks_frame(("p1", "abrupt", 100, 250), ("p2", "incipient", 1000, 1100)),
        h_values=[8.0, 12.0, 200.0],
        n_values=[1, 2],
    )

    row = row_of(table, 8.0, 1)
    assert (row["openings"], row["false_alarms"], row["repeat_openings"]) == (2, 1, 0)
    assert (row["detected"], row["total"]) == (1, 2)
    assert (row["detected_abrupt"], row["total_abrupt"], row["missed_abrupt"]) == (1, 1, ())
    assert (row["detected_incipient"], row["total_incipient"]) == (0, 1)
    assert row["missed_incipient"] == ("p2",)
    assert row["median_delay_start_abrupt"] == 2 * INTERVAL
    assert row["median_delay_peak_abrupt"] == 2 * INTERVAL
    assert pd.isna(row["median_delay_start_incipient"])
    # 1 false alarm over 300 windows of 5 minutes = 1500 minutes = 1500 / 10080 weeks.
    assert row["false_alarms_per_week"] == pytest.approx(6.72)

    assert row_of(table, 12.0, 1)["median_delay_start_abrupt"] == 3 * INTERVAL
    assert row_of(table, 12.0, 1)["false_alarms"] == 1
    for h in (8.0, 12.0):
        assert row_of(table, h, 2)["openings"] == 0
        assert row_of(table, h, 2)["missed_abrupt"] == ("p1",)
    nothing = row_of(table, 200.0, 1)
    assert (nothing["openings"], nothing["detected"]) == (0, 0)


def test_the_median_delay_is_over_the_detected_leaks_of_a_type() -> None:
    # Both steps are detected 2 windows after their start (w102, w202). pA starts at w100 with
    # its peak at w110, pB starts at w198 with its peak at w195: delays from start 2 and 4
    # windows (median 3), from peak -8 and +7 (the median of an even count is the mean: -0.5).
    table = run_sweep(
        two_steps(),
        leaks_frame(
            ("pA", "abrupt", 100, 250), ("pB", "abrupt", 198, 300), peaks={"pA": 110, "pB": 195}
        ),
        h_values=[8.0],
        n_values=[1],
    )

    row = row_of(table, 8.0, 1)
    assert row["detected_abrupt"] == 2
    assert row["median_delay_start_abrupt"] == 3 * INTERVAL
    assert row["median_delay_peak_abrupt"] == -0.5 * INTERVAL


def test_the_delay_columns_are_timedeltas_even_when_nothing_is_detected() -> None:
    table = run_sweep(two_steps(), leaks_frame(), h_values=[200.0], n_values=[1])

    assert str(table["median_delay_start_abrupt"].dtype).startswith("timedelta64")


def test_false_alarms_do_not_increase_with_h_for_one_sensor_without_a_cap() -> None:
    # One sensor, N = 1 and a cap out of reach (c = 1e6), so S does not depend on h: an alert
    # opens once per excursion of S above h_close that reaches h, and an excursion that reaches
    # a larger h reaches the smaller one too. With no labelled leak, every alert is a false
    # alarm. This is not a law of the detector: it needs a single sensor and no cap (below).
    h_values = [4.0, 6.0, 8.0, 12.0, 16.0, 24.0]
    for seed in (0, 1, 2, 3):
        rng = np.random.default_rng(seed)
        z = rng.normal(0.0, 1.0, (3000, 1))
        for start in rng.integers(0, 2900, size=12):
            z[start : start + int(rng.integers(5, 60))] += rng.uniform(1.0, 5.0)

        table = run_sweep(z, leaks_frame(), h_values=h_values, n_values=[1], c=1e6, h_close=4.0)

        false_alarms = list(table["false_alarms"])
        assert false_alarms == sorted(false_alarms, reverse=True), false_alarms
        assert false_alarms[0] > false_alarms[-1], "would hold trivially if nothing changed"


def test_a_larger_h_can_open_more_alerts_when_the_cap_is_active() -> None:
    # The monotonicity above is not general. One sensor, N = 1, c = 2, h_close = 4, k = 0.5.
    # z - k = +20, then -1 (8 times), +2.55, -0.7, then -1 until S is 0. With h = 5.0 the cap
    # is 10 and S falls 10 ... 2 in 8 steps; with h = 5.5 the cap is 11, so S is higher by 1
    # and falls 11 ... 3. After the rebound S is 4.55 (h = 5.0, below h) and 5.55 (h = 5.5,
    # above h): the larger h opens a second alert that the smaller one never opens.
    z = column(*(d + 0.5 for d in [20, *[-1] * 8, 2.55, -0.7, *[-1] * 12]))

    table = run_sweep(z, leaks_frame(), h_values=[5.0, 5.5], n_values=[1])

    assert list(table["false_alarms"]) == [1, 2]
    # The detector itself agrees, so this is not an artefact of the replay.
    for h, alerts in ((5.0, 1), (5.5, 2)):
        detector = Params(k=0.5, h=h, n_min=1, c=2.0, h_close=4.0)
        assert len(detector_openings(z, detector)) == alerts


def test_a_sweep_checks_the_leak_time_zone_even_if_nothing_opens() -> None:
    leaks = leaks_frame(("p1", "abrupt", 100, 250))
    for column in ("start", "end", "peak"):
        leaks[column] = leaks[column].dt.tz_localize(None)  # naive leaks, aware timestamps

    with pytest.raises(ValueError, match="time zone"):
        run_sweep(two_steps(), leaks, h_values=[200.0], n_values=[1])  # h = 200: no opening


def test_a_sweep_over_an_irregular_grid_is_refused() -> None:
    times = [ts(i) for i in (0, 1, 2, 4)]

    with pytest.raises(ValueError, match="regular"):
        sweep(
            np.zeros((4, 1)),
            times,
            leaks_frame(),
            k=0.5,
            h_values=[8.0],
            n_values=[1],
            c=2.0,
            h_close=4.0,
            match=RULE,
        )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"h_values": []}, "h_values"),
        ({"n_values": []}, "n_values"),
        ({"h_values": [8.0, 8.0]}, "h_values"),
        ({"n_values": [1, 1]}, "n_values"),
        ({"h_values": [8.0, 3.0]}, "h_close"),  # h = 3 is below h_close = 4: refused up front
        ({"n_values": [1, 5]}, "n_min"),  # more than the 2 sensors
    ],
)
def test_a_sweep_grid_the_detector_would_refuse_is_refused_before_replaying(
    overrides: dict[str, list[float]], match: str
) -> None:
    grid: dict[str, list[float]] = {"h_values": [8.0, 12.0], "n_values": [1, 2]}
    grid.update(overrides)

    with pytest.raises(ValueError, match=match):
        run_sweep(
            two_steps(),
            leaks_frame(),
            h_values=grid["h_values"],
            n_values=[int(n) for n in grid["n_values"]],
        )


def test_a_sweep_needs_two_timestamps_to_know_the_period() -> None:
    with pytest.raises(ValueError, match="period"):
        run_sweep(np.zeros((1, 1)), leaks_frame(), h_values=[8.0], n_values=[1])


def test_the_functions_of_the_sweep_have_no_parameter_with_a_default() -> None:
    for function in (replay, sweep, trajectory):
        defaults = [
            name
            for name, parameter in inspect.signature(function).parameters.items()
            if parameter.default is not inspect.Parameter.empty
        ]
        assert defaults == [], f"{function.__name__} has defaults: {defaults}"


# --- 5. The S trajectory (BE-012j) ---------------------------------------------------------


def detector_cusum(z: np.ndarray, p: Params) -> np.ndarray:
    """``residuals[sensor].cusum`` of ``CusumDetector.evaluate`` at every window (T x n)."""
    sensors = [f"P{i:02d}" for i in range(z.shape[1])]
    artefact = make_artefact(
        dict.fromkeys(sensors, 1.0), k=p.k, h=p.h, n_min=p.n_min, c=p.c, h_close=p.h_close
    )
    detector = CusumDetector(artefact, interval=INTERVAL)
    expected = dict.fromkeys(sensors, 0.0)
    rows = []
    for t in range(len(z)):
        observed = {s: -float(z[t, j]) for j, s in enumerate(sensors)}
        result = detector.evaluate(observed, expected, ts(t))
        rows.append([result.residuals[s].cusum for s in sensors])
    return np.array(rows, dtype=np.float64)


@pytest.mark.parametrize("quantised", [False, True])
@pytest.mark.parametrize("p", PARITY_PARAMS)
def test_trajectory_is_the_s_of_the_detector_bit_for_bit(p: Params, quantised: bool) -> None:
    z = parity_z(0, quantised=quantised)

    got = trajectory(z, k=p.k, h=p.h, c=p.c)

    np.testing.assert_array_equal(got, detector_cusum(z, p))  # exact, not approximate


def test_every_opening_statistic_is_read_on_the_trajectory() -> None:
    # With N = 1 the statistic of an alert is the largest S of its unclaimed sensors.
    z = parity_z(1, quantised=False)
    p = PARITY_PARAMS[0]
    s = trajectory(z, k=p.k, h=p.h, c=p.c)

    for opening in replay_params(z, p):
        assert opening.statistic == s[opening.index, opening.sensors[0]]
        assert all(s[opening.index, j] > p.h for j in opening.sensors)


@pytest.mark.parametrize(
    ("z", "params", "match"),
    [
        (np.zeros(6), {}, "2-D"),
        (np.zeros((6, 2), dtype=np.float32), {}, "float64"),
        (np.full((6, 2), np.nan), {}, "finite"),
        (np.zeros((6, 2)), {"k": 0.0}, "k must be positive"),
        (np.zeros((6, 2)), {"h": -1.0}, "h must be positive"),
        (np.zeros((6, 2)), {"c": 1.0}, "c must be finite and above 1"),
    ],
)
def test_trajectory_refuses_what_the_detector_would_refuse(
    z: np.ndarray, params: dict[str, float], match: str
) -> None:
    arguments = {"k": 0.5, "h": 8.0, "c": 2.0, **params}

    with pytest.raises(ValueError, match=match):
        trajectory(z, **arguments)
