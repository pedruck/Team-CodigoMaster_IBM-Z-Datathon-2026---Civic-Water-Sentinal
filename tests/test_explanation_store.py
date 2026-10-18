"""Stored, audited explanations generated in the background (P5). Fake narrators only."""

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.audit import (
    AuditError,
    HashChainAuditSink,
    canonical_json,
    verify_explanation,
    verify_file,
)
from sentinel.audit.devserver import (
    ExplainingAuditSink,
    create_app,
    narrator_from_env,
)
from sentinel.config import ConfigError
from sentinel.narrative import render_text
from sentinel.narrative.mock import mock_alert
from sentinel.narrative.store import (
    RECORD_TYPE,
    ExplanationService,
    ExplanationStore,
    explanations_path_for,
)
from sentinel.schemas.alerts import AuditEvent

GOOD = (
    "[SIMULATION] Alert A-20190314T1230Z-1 reports a suspected leak in region R4. "
    "Sensors P14 and P21 read 0.91 m and 0.62 m below the expected pressure since "
    "2019-03-14 02:10 UTC."
)


class Counting:
    """Fake model: answers ``replies`` in turn (an Exception instance is raised)."""

    def __init__(self, *replies: str | Exception) -> None:
        self.name = "fake-granite@0"
        self.replies = list(replies) or [GOOD]
        self.calls = 0
        self.release = threading.Event()
        self.release.set()

    def generate(self, prompt: str) -> str:
        self.release.wait(5)
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        return reply


def service(narrator: Any, path: Path | None = None, **kwargs: Any) -> ExplanationService:
    return ExplanationService(narrator, ExplanationStore(path, fsync=False), **kwargs)


def test_pending_then_ready_and_the_model_is_called_once() -> None:
    fake = Counting()
    fake.release.clear()
    s = service(fake)
    alert = mock_alert()
    s.submit(alert)
    pending = s.status(alert.alert_id)
    assert pending is not None and pending.status == "pending"
    assert pending.text == render_text(alert) and pending.record is None

    fake.release.set()
    assert s.wait_idle(5)
    ready = s.status(alert.alert_id)
    assert ready is not None and ready.status == "ready"
    assert ready.source == "llm" and ready.text == GOOD and ready.generator == "fake-granite@0"
    assert ready.record is not None and ready.record.seq == 0

    s.submit(alert)  # same alert again: already final
    assert s.wait_idle(5) and fake.calls == 1
    assert len(s.store.records()) == 1


def test_explanations_are_english_only() -> None:
    s = service(Counting())
    s.submit(mock_alert())
    assert s.wait_idle(5)
    status = s.status("A-20190314T1230Z-1")
    assert status is not None and status.language == "en"
    assert [r["payload"]["language"] for r in s.store.records()] == ["en"]


def test_rejected_text_is_final_and_logged_with_the_model_text() -> None:
    fake = Counting("[SIMULATION] Sensor P07 dropped 7.3 m.")
    s = service(fake)
    alert = mock_alert()
    s.submit(alert)
    s.wait_idle(5)
    s.submit(alert)
    s.wait_idle(5)
    status = s.status(alert.alert_id)
    assert status is not None and status.source == "template"
    assert status.fallback_reason == "narrative_rejected" and fake.calls == 1
    (record,) = s.store.records()
    assert record["payload"]["model_text"] == "[SIMULATION] Sensor P07 dropped 7.3 m."


def test_model_errors_are_retried_up_to_max_attempts() -> None:
    fake = Counting(TimeoutError("busy"), TimeoutError("busy"), TimeoutError("busy"), GOOD)
    s = service(fake, max_attempts=3)
    alert = mock_alert()
    for _ in range(5):
        s.submit(alert)
        assert s.wait_idle(5)
    status = s.status(alert.alert_id)
    assert status is not None and status.source == "template"
    assert status.fallback_reason is not None and "TimeoutError" in status.fallback_reason
    assert fake.calls == 3  # never a 4th call
    assert [r["payload"]["attempt"] for r in s.store.records()] == [1, 2, 3]


def test_error_then_success_shows_the_generated_text() -> None:
    fake = Counting(TimeoutError("busy"), GOOD)
    s = service(fake)
    alert = mock_alert()
    s.submit(alert)
    s.wait_idle(5)
    s.submit(alert)
    s.wait_idle(5)
    status = s.status(alert.alert_id)
    assert status is not None and status.source == "llm" and status.record is not None
    assert status.record.seq == 1  # the latest record is the current one


def test_without_a_model_the_deterministic_text_is_stored() -> None:
    s = service(None)
    alert = mock_alert()
    s.submit(alert)
    s.wait_idle(5)
    status = s.status(alert.alert_id)
    assert status is not None and status.status == "ready" and status.generator is None
    assert status.text == render_text(alert)


def test_restart_reads_the_log_and_does_not_call_the_model_again(tmp_path: Path) -> None:
    path = tmp_path / "run.explanations.jsonl"
    first = service(Counting(), path)
    first.submit(mock_alert())
    first.wait_idle(5)
    first.close()

    fake = Counting()
    again = service(fake, path)
    assert again.status("A-20190314T1230Z-1") is not None  # known from the log
    again.submit(mock_alert())
    again.wait_idle(5)
    assert fake.calls == 0
    assert verify_file(path).ok


def test_unknown_alert_has_no_status() -> None:
    assert service(None).status("A-nope") is None


def test_explanation_record_is_tied_to_the_alert_record(tmp_path: Path) -> None:
    audit = HashChainAuditSink(fsync=False)
    s = service(Counting())
    sink = ExplainingAuditSink(audit, s)
    alert = mock_alert(audit=None)
    sink.append(
        AuditEvent(
            type="alert.raised",
            window_end=alert.window_end,
            alert_id=alert.alert_id,
            payload=alert.model_dump(mode="json", exclude={"audit"}),
        )
    )
    s.wait_idle(5)
    (alert_record,) = audit.records()
    (explanation_record,) = s.store.records()
    assert explanation_record["type"] == RECORD_TYPE
    assert verify_explanation(explanation_record, alert_record) is None
    assert explanation_record["payload"]["audit"] == {"seq": 0, "hash": alert_record["hash"]}

    edited = json.loads(canonical_json(explanation_record))
    edited["payload"]["text"] = "[SIMULATION] All fine."
    assert "text differs" in (verify_explanation(edited, alert_record) or "")


def test_queueing_failure_never_fails_a_recorded_audit_event() -> None:
    class Broken:
        def submit(self, alert: Any) -> None:
            raise RuntimeError("queue down")

    audit = HashChainAuditSink(fsync=False)
    sink = ExplainingAuditSink(audit, Broken())  # type: ignore[arg-type]
    alert = mock_alert(audit=None)
    ref = sink.append(
        AuditEvent(
            type="alert.raised",
            window_end=alert.window_end,
            alert_id=alert.alert_id,
            payload=alert.model_dump(mode="json", exclude={"audit"}),
        )
    )
    assert ref.seq == 0 and len(audit.records()) == 1


def test_store_failure_keeps_the_alert_pending(tmp_path: Path) -> None:
    path = tmp_path / "x.explanations.jsonl"
    s = service(Counting(), path)
    path.write_bytes(b"not a record\n")  # someone wrote to the log: the chain refuses
    alert = mock_alert()
    s.submit(alert)
    s.wait_idle(5)
    status = s.status(alert.alert_id)
    assert status is not None and status.status == "pending"
    with pytest.raises(AuditError):
        ExplanationStore(path)  # a broken log is never extended after a restart either


def test_paths_and_env_parsing(tmp_path: Path) -> None:
    assert explanations_path_for("r1", tmp_path) == tmp_path / "r1.explanations.jsonl"
    assert narrator_from_env({}) is None
    with pytest.raises(ConfigError):
        narrator_from_env({"SENTINEL_NARRATOR": "gpt"})
    with pytest.raises(ConfigError, match="model file not found"):
        narrator_from_env(
            {"SENTINEL_NARRATOR": "llamacpp", "LOCAL_LLM_MODEL_PATH": str(tmp_path / "x.gguf")}
        )


def test_devserver_explains_alerts_over_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: Callable[..., dict[str, Any]],
) -> None:
    """Backend with mocks (no narrator → deterministic text), real chain, real HTTP."""
    monkeypatch.setenv("SENTINEL_AUDIT_RUN", "e2e")
    monkeypatch.setenv("SENTINEL_AUDIT_DIR", str(tmp_path))
    monkeypatch.setenv("SENTINEL_MIN_HISTORY_WINDOWS", "3")
    monkeypatch.setenv("SENTINEL_HISTORY_CAPACITY", "6")
    monkeypatch.setenv("SENTINEL_SENSOR_REGISTRY", str(_registry(tmp_path)))
    app = create_app()
    client = TestClient(app)
    for i in range(5):
        assert client.post("/v1/windows", json=payload(i)).status_code == 201
    r = client.post("/v1/windows", json=payload(5, drop_m=5.0, drop_sensors=("P1", "P2")))
    (alert_id,) = r.json()["alert_ids"]
    assert app.state.explanations.wait_idle(5)

    body = client.get(f"/v1/alerts/{alert_id}/explanation").json()
    assert body["status"] == "ready" and body["language"] == "en"
    assert body["text"].startswith("[SIMULATION]") and body["record"]["seq"] == 0
    missing = client.get("/v1/alerts/A-nope/explanation")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"

    assert verify_file(tmp_path / "e2e.jsonl").ok
    assert verify_file(tmp_path / "e2e.explanations.jsonl").ok
    alert_record = next(
        json.loads(line)
        for line in (tmp_path / "e2e.jsonl").read_text().splitlines()
        if json.loads(line)["type"] == "alert.raised"
    )
    first = json.loads((tmp_path / "e2e.explanations.jsonl").read_text().splitlines()[0])
    assert verify_explanation(first, alert_record) is None


def _registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "provisional": True,
                "pressure": ["P1", "P2", "P3"],
                "flow": ["F1"],
                "tank_level": ["T1"],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_after_a_restart_the_latest_version_of_an_alert_is_returned(tmp_path: Path) -> None:
    """Review of PR #17: two stored versions of one alert_id; status() must give the newer."""
    path = tmp_path / "run.explanations.jsonl"
    first = service(None, path)
    v1, v2 = mock_alert(), mock_alert(region="R7")
    first.submit(v1)
    first.wait_idle(5)
    first.submit(v2)
    first.wait_idle(5)
    first.close()

    again = service(None, path)
    status = again.status(v1.alert_id)
    assert status is not None and status.record is not None
    assert status.record.seq == 1 and "region R7" in status.text
