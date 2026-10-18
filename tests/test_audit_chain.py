"""Hash-chained audit sink (P5): chaining, tamper detection, durability, pipeline link."""

import hashlib
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.audit import (
    GENESIS_HASH,
    AuditError,
    AuditSink,
    HashChainAuditSink,
    alert_payload_sha256,
    audit_path_for,
    canonical_json,
    verify_alert,
    verify_file,
    verify_records,
)
from sentinel.audit.devserver import create_app as create_devserver_app
from sentinel.audit.verify import main as verify_main
from sentinel.config import Settings
from sentinel.main import create_app
from sentinel.schemas.alerts import AuditEvent, AuditRef
from sentinel.schemas.windows import SensorWindow
from sentinel.services.pipeline import Pipeline
from tests.conftest import ts

CLOCK = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def event(i: int, **payload: Any) -> AuditEvent:
    return AuditEvent(type="window.scored", window_end=ts(i), payload={"i": i, **payload})


def sink_at(path: Path | None = None) -> HashChainAuditSink:
    return HashChainAuditSink(path, fsync=False, clock=lambda: CLOCK)


def fill(sink: HashChainAuditSink, n: int) -> list[AuditRef]:
    return [sink.append(event(i)) for i in range(n)]


def rewrite(path: Path, edit: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> None:
    records = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_bytes(b"".join(canonical_json(r) + b"\n" for r in edit(records)))


def test_sink_satisfies_the_backend_port() -> None:
    assert isinstance(sink_at(), AuditSink)


def test_agreed_vm_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    expected = tmp_path / "shared" / "civicwater" / "audit" / "replay-2019.jsonl"
    assert audit_path_for("replay-2019") == expected
    assert audit_path_for("r1", root=tmp_path) == tmp_path / "r1.jsonl"
    for bad in ("", "../x", "a/b", ".hidden", "x" * 65):
        with pytest.raises(ValueError):
            audit_path_for(bad)


def test_hash_follows_the_documented_formula() -> None:
    sink = sink_at()
    refs = fill(sink, 2)
    first, second = sink.records()
    body = {k: v for k, v in first.items() if k not in ("prev_hash", "hash")}
    expected = hashlib.sha256(
        GENESIS_HASH.encode() + json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert first["prev_hash"] == GENESIS_HASH
    assert first["hash"] == expected == refs[0].hash
    assert second["prev_hash"] == first["hash"]
    assert [r.seq for r in refs] == [0, 1]
    assert first["payload_sha256"] == hashlib.sha256(b'{"i":0}').hexdigest()
    assert first["recorded_at"] == "2026-10-17T12:00:00Z"
    assert first["window_end"] == "2019-01-01T00:00:00Z"


def test_untouched_chain_verifies_in_memory_and_on_disk(tmp_path: Path) -> None:
    mem, disk = sink_at(), sink_at(tmp_path / "audit" / "chain.jsonl")
    for s in (mem, disk):
        fill(s, 5)
        check = s.verify()
        assert check.ok and check.records == 5
        assert check.head == s.head == AuditRef(seq=4, hash=s.records()[-1]["hash"])
    assert mem.records() == disk.records()  # same clock and events: same chain


@pytest.mark.parametrize(
    ("edit", "broken_at", "reason"),
    [
        (lambda rs: [*rs[:2], {**rs[2], "payload": {"i": 99}}, *rs[3:]], 2, "payload altered"),
        (
            lambda rs: [*rs[:2], {**rs[2], "window_end": "2019-01-02T00:00:00Z"}, *rs[3:]],
            2,
            "record altered",
        ),
        (lambda rs: [*rs[:2], *rs[3:]], 2, "sequence break"),
        (lambda rs: [rs[0], rs[2], rs[1], *rs[3:]], 1, "sequence break"),
        (lambda rs: [*rs[:2], {**rs[2], "extra": 1}, *rs[3:]], 2, "unexpected fields"),
    ],
    ids=["payload", "metadata", "removed", "reordered", "added-field"],
)
def test_tampering_is_detected_at_the_first_bad_record(
    tmp_path: Path,
    edit: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    broken_at: int,
    reason: str,
) -> None:
    path = tmp_path / "chain.jsonl"
    fill(sink_at(path), 5)
    rewrite(path, edit)
    check = verify_file(path)
    assert not check.ok
    assert check.broken_at == broken_at
    assert check.reason is not None and reason in check.reason


def test_renumbered_removal_still_breaks_the_links() -> None:
    sink = sink_at()
    fill(sink, 4)
    rs = sink.records()
    forged = [rs[0], {**rs[2], "seq": 1}, {**rs[3], "seq": 2}]
    check = verify_records(forged)
    assert not check.ok and check.broken_at == 1


def test_partial_line_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "chain.jsonl"
    fill(sink_at(path), 3)
    with path.open("ab") as f:
        f.write(b'{"seq": 3, "pay')
    check = verify_file(path)
    assert not check.ok and check.broken_at == 3 and check.records == 3


def test_consistent_rewrite_needs_the_external_anchor(tmp_path: Path) -> None:
    real = sink_at(tmp_path / "real.jsonl")
    fill(real, 4)
    anchor = real.head
    forged_path = tmp_path / "forged.jsonl"
    forged = sink_at(forged_path)
    for i in range(4):  # an attacker with write access recomputes every hash
        forged.append(event(i, note="rewritten"))
    assert verify_file(forged_path).ok  # the chain alone cannot tell
    check = verify_file(forged_path, anchor)
    assert not check.ok and check.reason is not None and "anchor" in check.reason


def test_truncation_is_caught_by_the_anchor(tmp_path: Path) -> None:
    path = tmp_path / "chain.jsonl"
    sink = sink_at(path)
    fill(sink, 5)
    anchor = sink.head
    rewrite(path, lambda rs: rs[:3])
    assert verify_file(path).ok
    check = verify_file(path, anchor)
    assert not check.ok and check.reason is not None and "truncated" in check.reason


def test_older_anchor_still_verifies_a_grown_chain() -> None:
    sink = sink_at()
    fill(sink, 3)
    anchor = sink.head
    fill(sink, 2)
    assert sink.verify(anchor).ok


def test_restart_continues_the_same_chain(tmp_path: Path) -> None:
    path = tmp_path / "chain.jsonl"
    fill(sink_at(path), 3)
    again = sink_at(path)
    ref = again.append(event(3))
    assert ref.seq == 3
    check = verify_file(path)
    assert check.ok and check.records == 4 and check.head == ref


def test_broken_file_is_never_extended(tmp_path: Path) -> None:
    path = tmp_path / "chain.jsonl"
    fill(sink_at(path), 3)
    rewrite(path, lambda rs: [rs[0], {**rs[1], "payload": {"i": 7}}, rs[2]])
    with pytest.raises(AuditError, match="record 1"):
        sink_at(path)


def test_failed_append_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "chain.jsonl"
    sink = HashChainAuditSink(path, fsync=True, clock=lambda: CLOCK)
    fill(sink, 2)
    size = path.stat().st_size

    def disk_full(fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("sentinel.audit.chain.os.fsync", disk_full)
    with pytest.raises(AuditError, match="No space left"):
        sink.append(event(2))
    assert path.stat().st_size == size  # the half-written line was removed
    monkeypatch.undo()
    assert sink.append(event(2)).seq == 2
    assert verify_file(path).ok


def test_non_json_payload_is_rejected_without_consuming_a_seq() -> None:
    sink = sink_at()
    with pytest.raises(AuditError, match="canonical JSON"):
        sink.append(event(0, bad=float("nan")))
    with pytest.raises(AuditError):
        sink.append(event(0, bad=object()))
    assert sink.append(event(0)).seq == 0


def test_stored_payload_is_isolated_from_the_caller() -> None:
    sink = sink_at()
    payload: dict[str, Any] = {"ids": ("a", "b")}
    sink.append(AuditEvent(type="window.scored", window_end=ts(0), payload=payload))
    payload["ids"] = ("changed",)
    assert sink.records()[0]["payload"] == {"ids": ["a", "b"]}
    assert sink.verify().ok


def test_concurrent_appends_keep_one_linear_chain() -> None:
    sink = sink_at()

    def worker(base: int) -> None:
        for i in range(50):
            sink.append(event(base + i))

    threads = [threading.Thread(target=worker, args=(k * 100,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check = sink.verify()
    assert check.ok and check.records == 400
    assert [r["seq"] for r in sink.records()] == list(range(400))


# ------------------------------------------------------------------ pipeline + HTTP link


def test_pipeline_alert_is_linked_to_its_record(
    tmp_path: Path,
    settings: Settings,
    make_pipeline: Callable[..., Pipeline],
    window: Callable[..., SensorWindow],
) -> None:
    """Mock forecaster/detector (backend's), real chain: plumbing only, not detection."""
    sink = sink_at(tmp_path / "chain.jsonl")
    p = make_pipeline(audit_sink=sink)
    for i in range(5):
        p.ingest(window(i))
    r = p.ingest(window(5, drop_m=5.0, drop_sensors=("P1", "P2"))).result
    assert r.audit == "recorded" and len(r.alert_ids) == 1

    records = sink.records()
    assert [x["type"] for x in records].count("window.not_scored") == 3
    assert [x["type"] for x in records].count("window.scored") == 3
    assert sink.verify().ok

    alert = p.alerts.get(r.alert_ids[0])
    assert alert is not None and alert.audit is not None and alert.audit.hash is not None
    record = sink.get(alert.audit.seq)
    assert record is not None
    assert verify_alert(alert, record) is None
    assert record["payload"]["models"]["detector"]["is_mock"] is True

    # The dashboard sees the alert through HTTP: the JSON must hash to the same value.
    client = TestClient(create_app(settings, p))
    served = client.get(f"/v1/alerts/{alert.alert_id}").json()
    assert alert_payload_sha256(served) == record["payload_sha256"]
    assert verify_alert(served, record) is None

    tampered = {
        **served,
        "deviating_sensors": [{**served["deviating_sensors"][0], "deviation_m": -0.1}],
    }
    problem = verify_alert(tampered, record)
    assert problem is not None and "altered" in problem


def test_devserver_runs_the_backend_with_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_AUDIT_RUN", "devtest")
    monkeypatch.setenv("SENTINEL_AUDIT_DIR", str(tmp_path))
    app = create_devserver_app()
    sink = app.state.pipeline.audit_sink
    assert isinstance(sink, HashChainAuditSink) and sink.path == tmp_path / "devtest.jsonl"

    body = json.loads(Path("docs/examples/window.valid.json").read_text(encoding="utf-8"))
    response = TestClient(app).post("/v1/windows", json=body)
    assert response.status_code == 201 and response.json()["audit"] == "recorded"
    check = verify_file(tmp_path / "devtest.jsonl")
    assert check.ok and check.records == 1

    # A restart continues the same log.
    again = create_devserver_app()
    assert again.state.pipeline.audit_sink.head == check.head


def test_verify_alert_without_audit_reference() -> None:
    sink = sink_at()
    fill(sink, 1)
    problem = verify_alert({"alert_id": "A-1", "audit": None}, sink.records()[0])
    assert problem is not None and "no audit reference" in problem


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "chain.jsonl"
    sink = sink_at(path)
    fill(sink, 3)
    head = sink.head
    assert head is not None and head.hash is not None
    assert verify_main([str(path), "--anchor-seq", "2", "--anchor-hash", head.hash]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    rewrite(path, lambda rs: [rs[0], {**rs[1], "payload": {"i": 5}}, rs[2]])
    assert verify_main([str(path)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["broken_at"] == 1


def test_file_changed_outside_the_sink_is_refused(tmp_path: Path) -> None:
    """Fail-closed retries depend on AuditError meaning "not stored": never write a second
    record with a seq that another writer may already have used."""
    path = tmp_path / "chain.jsonl"
    sink = sink_at(path)
    fill(sink, 2)
    other = sink_at(path)  # a second process on the same file
    other.append(event(2))
    size = path.stat().st_size
    with pytest.raises(AuditError, match="changed outside this sink"):
        sink.append(event(3))
    assert path.stat().st_size == size
    assert verify_file(path).ok  # nothing was corrupted


def test_records_after_pages_in_seq_order(tmp_path: Path) -> None:
    for sink in (sink_at(), sink_at(tmp_path / "chain.jsonl")):
        fill(sink, 5)
        assert [r["seq"] for r in sink.records_after(limit=2)] == [0, 1]
        assert [r["seq"] for r in sink.records_after(after_seq=1, limit=2)] == [2, 3]
        assert [r["seq"] for r in sink.records_after(after_seq=3)] == [4]
        assert sink.records_after(after_seq=4) == []
        assert sink.records_after(after_seq=1, limit=1)[0] == sink.records()[2]  # as stored
        with pytest.raises(ValueError):
            sink.records_after(limit=0)
