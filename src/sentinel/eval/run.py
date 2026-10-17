"""Layer-2 run recorder (ADR-010 §8): replay windows through the API and keep everything.

Posts every window of a JSONL file, in order, to a running API (stdlib HTTP, as
``scripts/replay.py``) and writes, in ``out``:

- ``responses.jsonl``: one line per window — line number, HTTP code, client latency and the
  ``WindowResult`` (or the error body);
- ``alerts.jsonl``: every alert named in a ``WindowResult``, fetched by ID, in that order;
- ``status.json`` (final ``/v1/status``) and ``run.json`` (run id, date, note).

``alerts.jsonl`` + ``status.json`` + ``run.json`` is the dashboard's playback format. A change of
``/v1/status.started_at`` during the run means the server restarted and the CUSUM state was
lost (ADR-003): the run is invalid and ``RunInvalidError`` is raised.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import StatusResponse, WindowResult

# `/v1/status.started_at` is recomputed as now - uptime on every call, so it jitters by
# microseconds (Q-DOC-03); a real restart moves it by at least the restart time.
RESTART_TOLERANCE = timedelta(seconds=1)


class RunInvalidError(RuntimeError):
    """The run cannot be evaluated (server restarted mid-run)."""


@dataclass(frozen=True, slots=True)
class RunSummary:
    windows: int
    rejected_by_code: dict[int, int]  # non-2xx answers per HTTP code (details in responses)
    alerts: int
    missing_alerts: int  # alert IDs announced but not served (e.g. withheld by the audit)
    started_at: datetime

    @property
    def rejected(self) -> int:
        return sum(self.rejected_by_code.values())

    @property
    def accepted(self) -> int:
        return self.windows - self.rejected


def _request(url: str, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    headers = {"content-type": "application/json"} if body is not None else {}
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _status(api: str, timeout: float) -> tuple[StatusResponse, bytes]:
    code, body = _request(f"{api}/v1/status", None, timeout)
    if code != 200:
        raise RunInvalidError(f"/v1/status answered {code}")
    return StatusResponse.model_validate_json(body), body


def record_run(
    windows: Path,
    api: str,
    out: Path,
    *,
    run_id: str,
    note: str = "",
    timeout: float = 10.0,
    check_every: int = 288,
) -> RunSummary:
    api = api.rstrip("/")
    out.mkdir(parents=True, exist_ok=True)
    before = _status(api, timeout)[0]
    if before.counters.get("windows_accepted", 0):
        raise RunInvalidError(
            f"not a fresh API instance: it already accepted "
            f"{before.counters['windows_accepted']} windows (ADR-010 §8)"
        )
    started_at = before.started_at
    alert_ids: list[str] = []
    rejected: dict[int, int] = {}
    count = 0
    with windows.open() as source, (out / "responses.jsonl").open("w") as responses:
        for count, line in enumerate((x for x in source if x.strip()), 1):
            began = time.perf_counter()
            code, body = _request(f"{api}/v1/windows", line.strip().encode(), timeout)
            latency_ms = (time.perf_counter() - began) * 1000.0
            record: dict[str, object] = {"line": count, "http": code, "latency_ms": latency_ms}
            if code in (200, 201):
                result = WindowResult.model_validate_json(body)
                alert_ids.extend(result.alert_ids)
                record["result"] = result.model_dump(mode="json")
            else:
                rejected[code] = rejected.get(code, 0) + 1
                try:
                    record["error"] = json.loads(body)
                except ValueError:
                    record["error"] = body.decode(errors="replace")
            responses.write(json.dumps(record) + "\n")
            if count % check_every == 0:
                _same_server(api, started_at, timeout)
    _same_server(api, started_at, timeout)

    missing = 0
    with (out / "alerts.jsonl").open("w") as alerts:
        for alert_id in alert_ids:
            code, body = _request(f"{api}/v1/alerts/{alert_id}", None, timeout)
            if code != 200:
                missing += 1
                continue
            alerts.write(Alert.model_validate_json(body).model_dump_json() + "\n")
    status, raw = _status(api, timeout)
    (out / "status.json").write_text(json.dumps(json.loads(raw), indent=2) + "\n")
    meta = {"run_id": run_id, "recorded_at": f"{datetime.now(UTC):%Y-%m-%d}", "note": note}
    (out / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    return RunSummary(
        windows=count,
        rejected_by_code=rejected,
        alerts=len(alert_ids) - missing,
        missing_alerts=missing,
        started_at=status.started_at,
    )


def _same_server(api: str, started_at: datetime, timeout: float) -> None:
    now = _status(api, timeout)[0].started_at
    if abs(now - started_at) > RESTART_TOLERANCE:
        raise RunInvalidError(
            f"server restarted during the run (started_at {started_at} -> {now}): "
            "detector state lost, run invalid (ADR-003, ADR-010 §8)"
        )
