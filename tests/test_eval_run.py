"""Layer-2 run recorder (ADR-010 §8), through a real HTTP server.

A real ``uvicorn`` serves the backend (mocks, synthetic day with a pressure step) in a thread;
the recorder posts the windows over HTTP like the 2019 run will.
"""

import hashlib
import json
import socket
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from dashboard.source import AlertQuery, PlaybackAlertSource
from sentinel.config import Settings
from sentinel.eval.__main__ import calibration_id, main
from sentinel.eval.labels import OFFICIAL_2019_LEAKS, check_official_labels, read_labels
from sentinel.eval.run import RunInvalidError, record_run
from sentinel.main import build_pipeline, create_app
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import generate


def serve(app: object) -> Iterator[str]:
    """Real uvicorn on a socket bound and listening before the thread starts: requests queue
    until the server accepts them, so the test never waits (docs/testing.md)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))  # type: ignore[arg-type]
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True
    thread.join(5)
    sock.close()


@pytest.fixture
def api() -> Iterator[str]:
    settings = Settings(audit_sink="memory")
    yield from serve(create_app(settings, build_pipeline(settings)))


@pytest.fixture
def windows(tmp_path: Path) -> Path:
    registry = SensorRegistry.load(Settings().sensor_registry_path)
    day = generate(
        datetime(2019, 3, 14, tzinfo=UTC),
        288,
        registry,
        seed=7,
        noise_m=0.05,
        leak_at=200,
        leak_sensors=registry.pressure[:3],
    )
    path = tmp_path / "windows.jsonl"
    path.write_text("".join(json.dumps(w) + "\n" for w in day))
    return path


def test_records_every_response_and_alert_and_plays_back(
    api: str, windows: Path, tmp_path: Path
) -> None:
    out = tmp_path / "run"

    summary = record_run(windows, api, out, run_id="test-run")

    responses = [json.loads(line) for line in (out / "responses.jsonl").read_text().splitlines()]
    assert len(responses) == 288
    assert {r["http"] for r in responses} == {201}
    assert all(r["latency_ms"] > 0 for r in responses)
    assert summary.windows == 288
    assert summary.rejected == 0
    assert summary.alerts == 1
    source = PlaybackAlertSource(out)
    assert source.list_alerts(AlertQuery()).total == 1
    assert source.status().counters["windows_accepted"] == 288
    assert source.recording is not None and source.recording.run_id == "test-run"


def test_a_server_that_already_received_windows_is_refused(
    api: str, windows: Path, tmp_path: Path
) -> None:
    record_run(windows, api, tmp_path / "first", run_id="first")

    with pytest.raises(RunInvalidError, match="fresh"):
        record_run(windows, api, tmp_path / "second", run_id="second")


def test_rejections_are_counted_by_code_and_kept_even_without_json(
    api: str, windows: Path, tmp_path: Path
) -> None:
    lines = windows.read_text().splitlines()
    conflict = json.loads(lines[1])  # same timestamp, different data → 409 window_conflict
    first_sensor = next(iter(conflict["pressures"]))
    conflict["pressures"][first_sensor] += 1.0
    windows.write_text("\n".join([*lines[:3], "not json", json.dumps(conflict), *lines[3:]]) + "\n")

    summary = record_run(windows, api, tmp_path / "run", run_id="t")

    assert summary.windows == 290
    assert summary.rejected_by_code == {409: 1, 422: 1}
    responses = (tmp_path / "run" / "responses.jsonl").read_text().splitlines()
    assert "error" in json.loads(responses[3])


class RestartingApi:
    """Answers like the backend (bodies captured from it) but restarts after ``after`` status
    calls: ``started_at`` jumps by 60 s."""

    def __init__(self, status: dict[str, object], result: bytes, after: int) -> None:
        self.status_calls = 0
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                api.status_calls += 1
                body = dict(status)
                if api.status_calls > after:
                    started = datetime.fromisoformat(str(status["started_at"]))
                    body["started_at"] = (started + timedelta(seconds=60)).isoformat()
                self._send(200, json.dumps(body).encode())

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["content-length"]))
                self._send(201, result)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


def test_a_restart_during_the_run_invalidates_it(windows: Path, tmp_path: Path) -> None:
    settings = Settings(audit_sink="memory")
    client = TestClient(create_app(settings, build_pipeline(settings)))
    fresh_status = client.get("/v1/status").json()  # before any window: a fresh instance
    first = json.loads(windows.read_text().splitlines()[0])
    result = client.post("/v1/windows", json=first).content
    api = RestartingApi(fresh_status, result, after=1)
    threading.Thread(target=api.server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RunInvalidError, match="restarted"):
            record_run(windows, api.url, tmp_path / "run", run_id="t", check_every=10)
    finally:
        api.server.shutdown()


# --- orchestration: labels check, run, report, manifest --------------------------------------


def official_labels(path: Path) -> Path:
    rows = ["pipe,start,end,leak_size,type,peak"]
    rows += [f"{p},{s},{e},0.01,{t},{s}" for p, s, e, t in OFFICIAL_2019_LEAKS]
    path.write_text("\n".join(rows) + "\n")
    return path


def test_labels_that_differ_from_the_official_list_are_refused(tmp_path: Path) -> None:
    labels = official_labels(tmp_path / "leaks.csv")
    check_official_labels(read_labels(labels))
    text = labels.read_text().replace("p523,2019-01-15 23:00", "p523,2019-01-16 23:00")
    labels.write_text(text)

    with pytest.raises(ValueError, match="p523"):
        check_official_labels(read_labels(labels))


def test_cli_checks_labels_before_posting_anything(api: str, windows: Path, tmp_path: Path) -> None:
    labels = tmp_path / "leaks.csv"
    labels.write_text(
        "pipe,start,end,leak_size,type,peak\n"
        "p1,2019-01-01 00:00,2019-01-02 00:00,0,abrupt,2019-01-01 00:00\n"
    )
    out = tmp_path / "run"

    with pytest.raises(ValueError, match="official"):
        main(["--windows", str(windows), "--api", api, "--labels", str(labels), "--out", str(out)])
    assert not (out / "responses.jsonl").exists()


def test_cli_writes_report_and_manifest(api: str, windows: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    registry = Path(Settings().sensor_registry_path)

    code = main(
        [
            "--windows", str(windows), "--api", api,
            "--labels", str(official_labels(tmp_path / "leaks.csv")),
            "--out", str(out), "--registry", str(registry), "--run-id", "cli-test",
        ]
    )  # fmt: skip

    assert code == 0
    report = (out / "report.md").read_text()
    assert report.startswith("# Layer-2 evaluation — run cli-test")
    assert "**NOT A RESULT**" in report  # mocks and the provisional test registry
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["detector"]["name"] == "mock-threshold"
    assert manifest["registry_provisional"] is True
    assert set(manifest["data_sha256"]) == {"windows.jsonl", "leaks.csv"}
    assert len(manifest["lock_sha256"]) == 64


def test_no_code_outside_the_evaluation_reads_the_2019_labels() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = [
        *root.glob("src/**/*.py"),
        *root.glob("scripts/*.py"),
        *root.glob("dashboard/**/*.py"),
    ]
    offenders = [
        str(p.relative_to(root))
        for p in sources
        if "src/sentinel/eval" not in p.relative_to(root).as_posix()
        and ("leaks_2019" in p.read_text() or "eval/v1" in p.read_text())
    ]
    assert offenders == []


def test_cusum_run_requires_the_calibration_matching_the_detector_version(tmp_path: Path) -> None:
    calibration = tmp_path / "cal.json"
    calibration.write_text("{}")
    digest = hashlib.sha256(calibration.read_bytes()).hexdigest()

    assert calibration_id("cusum", f"0.1.0+{digest[:8]}", calibration) == digest
    assert calibration_id("mock-threshold", "0.1.0", None) is None
    with pytest.raises(ValueError, match="--calibration"):
        calibration_id("cusum", f"0.1.0+{digest[:8]}", None)
    with pytest.raises(ValueError, match="does not match"):
        calibration_id("cusum", "0.1.0+00000000", calibration)


def test_missing_alerts_watermark_the_report(api: str, windows: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    labels = official_labels(tmp_path / "leaks.csv")
    main(["--windows", str(windows), "--api", api, "--labels", str(labels), "--out", str(out)])
    manifest = json.loads((out / "manifest.json").read_text())

    assert manifest["is_mock"] is True
    assert manifest["protocol"].startswith("ADR-010 v1")
    assert manifest["year_start"] == "2019-01-01T00:00:00+00:00"
    report = (out / "report.md").read_text()
    assert "Windows: 288 accepted, 0 rejected" in report
    assert "Alerts announced but not fetched: 0" in report
