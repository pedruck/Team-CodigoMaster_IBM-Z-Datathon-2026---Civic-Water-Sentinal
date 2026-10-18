"""BE-013: the audit owner's hash chain and stored explanations selected by configuration
(``SENTINEL_AUDIT_SINK=chain``), and the read endpoints of Q-AUD-06 / Q-AUD-07."""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.audit.chain import verify_explanation, verify_records
from sentinel.config import ConfigError, Settings
from sentinel.main import build_pipeline, create_app
from sentinel.narrative.runtime import ExplainingAuditSink
from sentinel.sensors import SensorRegistry


def chain_settings(settings: Settings, tmp_path: Path, run: str = "run1") -> Settings:
    return replace(settings, audit_sink="chain", audit_run=run, audit_dir=tmp_path)


def chain_client(settings: Settings, registry: SensorRegistry) -> TestClient:
    return TestClient(create_app(settings, build_pipeline(settings, registry=registry)))


def post_with_alert(client: TestClient, payload: Callable[..., dict[str, Any]]) -> str:
    """5 normal windows, then one with a 5 m drop on two sensors: one mock alert."""
    for i in range(5):
        assert client.post("/v1/windows", json=payload(i)).status_code == 201
    r = client.post("/v1/windows", json=payload(5, drop_m=5.0, drop_sensors=("P1", "P2")))
    assert r.status_code == 201 and r.json()["audit"] == "recorded"
    (alert_id,) = r.json()["alert_ids"]
    return str(alert_id)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"SENTINEL_AUDIT_SINK": "chain"}, "requires SENTINEL_AUDIT_RUN"),
        ({"SENTINEL_AUDIT_SINK": "chain", "SENTINEL_AUDIT_RUN": "../etc"}, "SENTINEL_AUDIT_RUN"),
        ({"SENTINEL_AUDIT_SINK": "memory", "SENTINEL_AUDIT_RUN": "r"}, "only applies"),
        ({"SENTINEL_AUDIT_DIR": "/tmp/x"}, "only applies"),
        ({"SENTINEL_NARRATOR": "watsonx"}, "needs SENTINEL_AUDIT_SINK=chain"),
        ({"SENTINEL_NARRATOR": "gpt"}, "SENTINEL_NARRATOR"),
        ({"SENTINEL_AUDIT_SINK": "file"}, "SENTINEL_AUDIT_SINK"),
        (
            {"SENTINEL_AUDIT_SINK": "chain", "SENTINEL_AUDIT_RUN": "r", "SENTINEL_AUDIT_DIR": ""},
            "must not be empty",
        ),
    ],
)
def test_invalid_audit_configuration_is_refused(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_chain_configuration_names_the_log(tmp_path: Path) -> None:
    s = Settings.from_env(
        {
            "SENTINEL_AUDIT_SINK": "chain",
            "SENTINEL_AUDIT_RUN": "replay-2019",
            "SENTINEL_AUDIT_DIR": str(tmp_path),
        }
    )
    assert s.audit_path == tmp_path / "replay-2019.jsonl" and s.narrator == "none"
    assert Settings().audit_path is None


def test_chain_records_every_window_and_is_readable_over_http(
    settings: Settings,
    registry: SensorRegistry,
    payload: Callable[..., dict[str, Any]],
    tmp_path: Path,
) -> None:
    s = chain_settings(settings, tmp_path)
    client = chain_client(s, registry)
    alert_id = post_with_alert(client, payload)
    assert client.get("/v1/status").json()["audit_sink"] == "hash-chain"

    head = client.get("/v1/audit/head").json()
    assert head["seq"] == 6  # 6 window events + 1 alert.raised

    first = client.get("/v1/audit", params={"limit": 4}).json()
    assert [r["seq"] for r in first["items"]] == [0, 1, 2, 3]
    assert first["next_after_seq"] == 3
    rest = client.get("/v1/audit", params={"after_seq": 3, "limit": 4}).json()
    assert [r["seq"] for r in rest["items"]] == [4, 5, 6] and rest["next_after_seq"] is None
    assert client.get("/v1/audit", params={"after_seq": 6}).json()["items"] == []

    records = first["items"] + rest["items"]
    check = verify_records(records)
    assert check.ok and check.head is not None and check.head.hash == head["hash"]
    raised = [r for r in records if r["type"] == "alert.raised"]
    assert [r["alert_id"] for r in raised] == [alert_id]
    # The alert served over HTTP points at its record in the chain.
    assert client.get(f"/v1/alerts/{alert_id}").json()["audit"]["seq"] == raised[0]["seq"]

    assert client.get("/v1/audit", params={"limit": 0}).status_code == 422
    assert client.get("/v1/audit", params={"after_seq": -2}).status_code == 422


def test_alert_explanation_is_stored_and_served(
    settings: Settings,
    registry: SensorRegistry,
    payload: Callable[..., dict[str, Any]],
    tmp_path: Path,
) -> None:
    s = chain_settings(settings, tmp_path)
    app = create_app(s, build_pipeline(s, registry=registry))
    client = TestClient(app)
    alert_id = post_with_alert(client, payload)
    assert app.state.explanations.wait_idle(5)

    body = client.get(f"/v1/alerts/{alert_id}/explanation").json()
    assert body["status"] == "ready" and body["source"] == "template"
    assert body["text"].startswith("[SIMULATION]") and body["record"]["seq"] == 0
    missing = client.get("/v1/alerts/A-nope/explanation")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"

    alert_record = next(
        r for r in client.get("/v1/audit").json()["items"] if r["type"] == "alert.raised"
    )
    stored = json.loads((tmp_path / "run1.explanations.jsonl").read_text().splitlines()[0])
    assert verify_explanation(stored, alert_record) is None


def test_restart_extends_the_same_chain(
    settings: Settings,
    registry: SensorRegistry,
    payload: Callable[..., dict[str, Any]],
    tmp_path: Path,
) -> None:
    s = chain_settings(settings, tmp_path)
    post_with_alert(chain_client(s, registry), payload)

    again = chain_client(s, registry)
    assert again.get("/v1/audit/head").json()["seq"] == 6
    # Detector and history restart empty (ADR-003); the chain continues at seq 7.
    assert again.post("/v1/windows", json=payload(10)).status_code == 201
    assert again.get("/v1/audit/head").json()["seq"] == 7
    assert verify_records(again.get("/v1/audit", params={"limit": 1000}).json()["items"]).ok


def test_broken_chain_stops_startup(
    settings: Settings,
    registry: SensorRegistry,
    payload: Callable[..., dict[str, Any]],
    tmp_path: Path,
) -> None:
    s = chain_settings(settings, tmp_path)
    post_with_alert(chain_client(s, registry), payload)
    path = tmp_path / "run1.jsonl"
    lines = path.read_text().splitlines()
    record = json.loads(lines[1])
    record["payload"]["status"] = "tampered"
    lines[1] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n")

    with pytest.raises(ConfigError, match="cannot open the audit chain"):
        build_pipeline(s, registry=registry)


@pytest.mark.parametrize("sink", ["null", "memory"])
def test_read_endpoints_need_the_chain(
    sink: str,
    settings: Settings,
    registry: SensorRegistry,
    payload: Callable[..., dict[str, Any]],
) -> None:
    s = replace(settings, audit_sink=sink)
    pipeline = build_pipeline(s, registry=registry)
    assert not isinstance(pipeline.audit_sink, ExplainingAuditSink)
    client = TestClient(create_app(s, pipeline))
    alert_id = post_with_alert(client, payload) if sink == "memory" else "A-x"

    for path in ("/v1/audit", "/v1/audit/head", f"/v1/alerts/{alert_id}/explanation"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert "SENTINEL_AUDIT_SINK=chain" in r.json()["error"]["message"]
