"""Hardening of the write path found by the pre-integration review (2026-10-18):
physical limits, timestamps far ahead, write token, body size, and the replay tooling."""

import asyncio
import importlib.util
import json
from collections.abc import Callable, MutableMapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.config import ConfigError, Settings
from sentinel.main import build_pipeline, create_app
from sentinel.sensors import SensorRegistry, SensorRegistryFile
from tests.conftest import ts

REPO = Path(__file__).resolve().parents[1]
OFFICIAL = REPO / "config/sensors.battledim.json"
TOKEN = "t" * 32
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def limited(registry: SensorRegistry) -> SensorRegistry:
    return replace(registry, limits={"pressure": (0.0, 100.0), "flow": (0.0, 500.0)})


def client_for(
    settings: Settings, registry: SensorRegistry, clock: Callable[[], datetime] = lambda: NOW
) -> TestClient:
    pipeline = build_pipeline(settings, registry=registry)
    pipeline._clock = clock
    return TestClient(create_app(settings, pipeline))


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ physical limits


def test_impossible_readings_are_refused_and_leave_no_trace(
    settings: Settings, registry: SensorRegistry, payload: Callable[..., dict[str, Any]]
) -> None:
    """The review's case: four sensors at -1000 m and one at 10⁹ m became an alert."""
    client = client_for(settings, limited(registry))
    body = payload(0)
    body["pressures"] |= {"P1": -1000.0, "P2": -1000.0, "P3": 1e9}

    r = client.post("/v1/windows", json=body)

    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "reading_out_of_range"
    assert error["details"]["readings"] == {"P1": -1000.0, "P2": -1000.0, "P3": 1e9}
    status = client.get("/v1/status").json()
    assert status["last_timestamp"] is None and status["history"]["size"] == 0
    assert status["counters"]["windows_rejected_reading_out_of_range"] == 1


def test_limits_are_inclusive_and_per_kind(
    settings: Settings, registry: SensorRegistry, payload: Callable[..., dict[str, Any]]
) -> None:
    client = client_for(settings, limited(registry))
    edge = payload(0)
    edge["pressures"] |= {"P1": 0.0, "P2": 100.0}
    assert client.post("/v1/windows", json=edge).status_code == 201

    flow = payload(1)
    flow["flows"] = {"F1": 500.5}
    r = client.post("/v1/windows", json=flow)
    assert r.status_code == 422 and r.json()["error"]["details"]["readings"] == {"F1": 500.5}
    # tank_level has no limit here: any finite value passes.
    level = payload(2)
    level["tank_levels"] = {"T1": -3.0}
    assert client.post("/v1/windows", json=level).status_code == 201


def test_official_registry_carries_the_data_owners_cleaning_ranges() -> None:
    """Same numbers as ``cleaning.valid_ranges`` of data v1's MANIFEST.json."""
    assert SensorRegistry.load(OFFICIAL).limits == {
        "pressure": (0.0, 100.0),
        "flow": (0.0, 500.0),
        "tank_level": (0.0, 10.0),
    }


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"pressure": {"min": 5, "max": 5}}, "must be below"),
        ({"head": {"min": 0, "max": 1}}, "unknown"),
    ],
)
def test_invalid_limits_are_refused(limits: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SensorRegistryFile.model_validate(
            {"provisional": True, "pressure": ["P1"], "limits": limits}
        )


def test_registry_builder_takes_limits_from_the_manifest() -> None:
    from sentinel.data.build_registry import limites  # noqa: PLC0415  (imports pandas)

    manifest = {
        "cleaning": {"valid_ranges": {"Pressure_": [0, 100], "Flow_": [0, 500], "Level_": [0, 10]}}
    }
    on_disk = json.loads(OFFICIAL.read_text(encoding="utf-8"))["limits"]
    assert limites(manifest) == on_disk


# ------------------------------------------------------------------ timestamps far ahead


def test_window_after_the_server_clock_is_refused(
    settings: Settings, registry: SensorRegistry, payload: Callable[..., dict[str, Any]]
) -> None:
    client = client_for(settings, registry, clock=lambda: ts(0))
    ahead = payload(13)  # 65 min after the clock, tolerance 60 min
    r = client.post("/v1/windows", json=ahead)
    assert r.status_code == 422 and r.json()["error"]["code"] == "timestamp_in_future"
    # Nothing was accepted, so the real stream is not blocked.
    assert client.post("/v1/windows", json=payload(0)).status_code == 201
    assert client.post("/v1/windows", json=payload(12)).status_code == 201  # exactly +60 min


def test_typo_in_the_year_does_not_block_the_stream(
    settings: Settings, registry: SensorRegistry, payload: Callable[..., dict[str, Any]]
) -> None:
    """The review's case: a 2099 window accepted, then every real one ``out_of_order``."""
    client = client_for(settings, registry, clock=lambda: datetime(2100, 1, 1, tzinfo=UTC))
    assert client.post("/v1/windows", json=payload(0)).status_code == 201
    typo = payload(0)
    typo["timestamp"] = "2099-01-01T00:05:00Z"

    r = client.post("/v1/windows", json=typo)

    assert r.status_code == 409 and r.json()["error"]["code"] == "gap_too_large"
    assert r.json()["error"]["details"]["max_gap_seconds"] == 86_400
    assert client.post("/v1/windows", json=payload(1)).status_code == 201


def test_gap_up_to_the_limit_is_accepted_and_zero_disables_it(
    settings: Settings, registry: SensorRegistry, payload: Callable[..., dict[str, Any]]
) -> None:
    day = 288  # windows
    client = client_for(settings, registry, clock=lambda: ts(10 * day))
    assert client.post("/v1/windows", json=payload(0)).status_code == 201
    assert client.post("/v1/windows", json=payload(day)).status_code == 201  # exactly 1 day
    assert client.post("/v1/windows", json=payload(2 * day + 1)).status_code == 409

    open_ = client_for(replace(settings, max_gap_seconds=0), registry, clock=lambda: ts(10 * day))
    assert open_.post("/v1/windows", json=payload(0)).status_code == 201
    assert open_.post("/v1/windows", json=payload(5 * day)).status_code == 201


# ------------------------------------------------------------------ write token and body size


def test_post_needs_the_token_reads_do_not(
    settings: Settings, registry: SensorRegistry, payload: Callable[..., dict[str, Any]]
) -> None:
    client = client_for(replace(settings, ingest_token=TOKEN), registry)

    missing = client.post("/v1/windows", json=payload(0))
    assert missing.status_code == 401 and missing.json()["error"]["code"] == "unauthorized"
    assert missing.headers["www-authenticate"] == "Bearer"
    wrong = client.post("/v1/windows", json=payload(0), headers={"authorization": "Bearer nope"})
    assert wrong.status_code == 401
    # Refused before the body is parsed: garbage without a token is 401, not 422.
    assert client.post("/v1/windows", content=b"{not json").status_code == 401

    ok = client.post("/v1/windows", json=payload(0), headers={"authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 201
    assert client.get("/v1/status").status_code == 200
    assert client.get("/v1/alerts").status_code == 200
    counters = client.get("/v1/status").json()["counters"]
    assert counters["windows_accepted"] == 1 and counters["http_windows_401"] == 3


def test_token_settings(tmp_path: Path) -> None:
    path = tmp_path / "ingest.token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    s = Settings.from_env({"SENTINEL_INGEST_TOKEN_FILE": str(path)})
    assert s.ingest_token == TOKEN
    assert TOKEN not in repr(s)
    with pytest.raises(ConfigError, match="not both"):
        Settings.from_env({"SENTINEL_INGEST_TOKEN": TOKEN, "SENTINEL_INGEST_TOKEN_FILE": str(path)})
    with pytest.raises(ConfigError, match="at least 16"):
        Settings.from_env({"SENTINEL_INGEST_TOKEN": "short"})
    with pytest.raises(ConfigError, match="cannot read"):
        Settings.from_env({"SENTINEL_INGEST_TOKEN_FILE": str(tmp_path / "absent")})
    assert Settings.from_env({}).ingest_token is None


def test_large_bodies_are_refused(
    settings: Settings, registry: SensorRegistry, payload: Callable[..., dict[str, Any]]
) -> None:
    client = client_for(replace(settings, max_body_bytes=2048), registry)
    body = payload(0)
    body["source"] = "x" * 100
    assert client.post("/v1/windows", json=body).status_code == 201
    big = json.dumps(payload(1) | {"padding": "x" * 4096}).encode()
    r = client.post("/v1/windows", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_body_limit_also_holds_without_content_length() -> None:
    """A chunked body is counted while it is read, not trusted from a header."""
    from sentinel.api.guard import WriteGuard  # noqa: PLC0415  (ASGI-level test)

    reached: list[bytes] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        reached.append((await receive())["body"])

    chunks = [{"type": "http.request", "body": b"x" * 600, "more_body": True}] * 3
    chunks.append({"type": "http.request", "body": b"", "more_body": False})
    sent: list[MutableMapping[str, Any]] = []

    async def run(limit: int) -> None:
        queue = list(chunks)

        async def receive() -> dict[str, Any]:
            return queue.pop(0)

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        scope = {"type": "http", "method": "POST", "headers": []}
        await WriteGuard(app, token=None, max_body_bytes=limit)(scope, receive, send)

    asyncio.run(run(1024))
    assert sent[0]["status"] == 413 and reached == []
    sent.clear()
    asyncio.run(run(4096))
    assert sent == [] and reached == [b"x" * 1800]


# ------------------------------------------------------------------ replay client


def test_replay_script_needs_an_explicit_url(tmp_path: Path) -> None:
    replay = load_script("replay")
    with pytest.raises(SystemExit):
        replay.main([str(tmp_path / "w.jsonl")])


def test_replay_script_checks_the_file_against_its_manifest(tmp_path: Path) -> None:
    replay = load_script("replay")
    windows = tmp_path / "windows.jsonl"
    windows.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    assert "no windows.jsonl.manifest.json" in replay.check_manifest(windows)

    import hashlib  # noqa: PLC0415

    sidecar = windows.with_name("windows.jsonl.manifest.json")
    digest = hashlib.sha256(windows.read_bytes()).hexdigest()
    sidecar.write_text(json.dumps({"sha256": digest, "windows": 2}), encoding="utf-8")
    assert "matches" in replay.check_manifest(windows)

    windows.write_text('{"a":1}\n{"a":3}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="does not match"):
        replay.check_manifest(windows)


def test_replay_script_sends_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    replay = load_script("replay")
    seen: list[str | None] = []

    class Response:
        status = 201

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def fake_urlopen(request: Any, timeout: float) -> Response:
        seen.append(request.get_header("Authorization"))
        return Response()

    monkeypatch.setattr(replay.urllib.request, "urlopen", fake_urlopen)
    assert replay.post("http://x/v1/windows", b"{}", 1.0, TOKEN) == 201
    assert replay.post("http://x/v1/windows", b"{}", 1.0) == 201
    assert seen == [f"Bearer {TOKEN}", None]
    monkeypatch.setenv("SENTINEL_INGEST_TOKEN", TOKEN)
    assert replay.read_token(None) == TOKEN
