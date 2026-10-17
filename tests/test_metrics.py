from sentinel.observability.metrics import Metrics


def test_percentiles_use_nearest_rank() -> None:
    m = Metrics()
    for v in range(1, 101):
        m.observe("forecast", v / 1000)  # 1..100 ms
    s = m.latency()["forecast"]
    assert (s.count, s.p50_ms, s.p95_ms, s.p99_ms, s.max_ms) == (100, 50.0, 95.0, 99.0, 100.0)


def test_empty_stage_reports_none() -> None:
    s = Metrics().latency()["detection"]
    assert s.count == 0 and s.p99_ms is None


def test_counters_accumulate() -> None:
    m = Metrics()
    m.inc("a")
    m.inc("a", 2)
    assert m.counters() == {"a": 3}
