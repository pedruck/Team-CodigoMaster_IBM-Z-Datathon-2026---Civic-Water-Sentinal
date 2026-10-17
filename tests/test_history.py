import threading
from collections.abc import Callable

import pytest

from sentinel.schemas.windows import SensorWindow
from sentinel.services.history import HistoryBuffer


@pytest.fixture
def buf() -> HistoryBuffer:
    return HistoryBuffer(capacity=4, interval_seconds=300)


def test_append_in_order_grows_contiguous(
    buf: HistoryBuffer, window: Callable[..., SensorWindow]
) -> None:
    for i in range(3):
        assert buf.append(window(i)) == 0
    assert len(buf) == 3
    assert buf.contiguous == 3


@pytest.mark.parametrize("i", [0, 1])
def test_repeated_or_older_timestamp_is_refused(
    buf: HistoryBuffer, window: Callable[..., SensorWindow], i: int
) -> None:
    buf.append(window(0))
    buf.append(window(1))
    with pytest.raises(ValueError):
        buf.append(window(i))
    assert len(buf) == 2


def test_gap_is_reported_and_resets_contiguity(
    buf: HistoryBuffer, window: Callable[..., SensorWindow]
) -> None:
    buf.append(window(0))
    buf.append(window(1))
    assert buf.append(window(4)) == 2
    assert len(buf) == 3
    assert buf.contiguous == 1
    assert [w.timestamp for w in buf.contiguous_tail()] == [window(4).timestamp]


def test_capacity_evicts_oldest(buf: HistoryBuffer, window: Callable[..., SensorWindow]) -> None:
    for i in range(10):
        buf.append(window(i))
    assert len(buf) == 4
    assert buf.contiguous == 4
    assert [w.timestamp for w in buf.contiguous_tail()] == [
        window(i).timestamp for i in range(6, 10)
    ]


def test_contiguous_tail_excluding_last_returns_only_past(
    buf: HistoryBuffer, window: Callable[..., SensorWindow]
) -> None:
    for i in range(3):
        buf.append(window(i))
    past = buf.contiguous_tail(exclude_last=True)
    assert [w.timestamp for w in past] == [window(0).timestamp, window(1).timestamp]
    assert buf.contiguous_tail(1, exclude_last=True)[0].timestamp == window(1).timestamp


def test_clear_simulates_restart(buf: HistoryBuffer, window: Callable[..., SensorWindow]) -> None:
    buf.append(window(0))
    buf.clear()
    assert len(buf) == 0
    assert buf.last_timestamp is None
    buf.append(window(0))


def test_concurrent_readers_and_writer_keep_invariants(
    window: Callable[..., SensorWindow],
) -> None:
    buf = HistoryBuffer(capacity=50, interval_seconds=300)
    windows = [window(i) for i in range(500)]
    errors: list[BaseException] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                tail = buf.contiguous_tail(exclude_last=True)
                stamps = [w.timestamp for w in tail]
                assert stamps == sorted(stamps)
                assert buf.contiguous <= buf.capacity
            except BaseException as exc:  # collected and asserted below
                errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    for w in windows:
        buf.append(w)
    stop.set()
    for t in readers:
        t.join()
    assert errors == []
    assert len(buf) == 50
    assert buf.contiguous == 50
