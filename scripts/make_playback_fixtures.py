"""Write the dashboard's playback fixture runs (``dashboard/playback/``).

Both runs come from the backend itself, in process, with the **mocks** over one synthetic day
(invented signal, official sensor IDs):

- ``fixture-empty``: no injected step → no alert; real status.
- ``fixture-edge-cases``: a pressure step on 3 sensors → the mock alert(s), plus hand-made
  variants of that alert covering the contract edges (every nullable field null, empty
  deviating-sensor list, every nullable field present). Variant values are invented for UI
  testing; they stay ``is_mock=true`` and the run note says so.
- ``fixture-malformed``: one valid mock alert and one record without ``models`` (contract
  violation on purpose) → the dashboard must show "contract mismatch".

Usage: ``uv run python scripts/make_playback_fixtures.py [out_dir]``.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from sentinel.config import Settings
from sentinel.main import build_pipeline, create_app
from sentinel.schemas.alerts import Alert, AuditRef
from sentinel.schemas.detections import CusumEvidence
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import generate

DAY = datetime(2019, 3, 14, tzinfo=UTC)
OFFICIAL_REGISTRY = Path("config/sensors.battledim.json")


def replay(leak: bool) -> tuple[list[Alert], str]:
    settings = Settings(audit_sink="memory", sensor_registry_path=OFFICIAL_REGISTRY)
    client = TestClient(create_app(settings, build_pipeline(settings)))
    registry = SensorRegistry.load(settings.sensor_registry_path)
    windows = generate(
        DAY,
        288,
        registry,
        seed=7,
        noise_m=0.05,
        leak_at=200 if leak else None,
        leak_sensors=registry.pressure[:3],
    )
    for w in windows:
        client.post("/v1/windows", json=w)
    page = client.get("/v1/alerts", params={"limit": 500}).json()
    alerts = [Alert.model_validate(a) for a in reversed(page["items"])]  # stored order
    return alerts, client.get("/v1/status").text


def edge_variants(base: Alert) -> list[Alert]:
    stamp = f"{base.window_end:%Y%m%dT%H%M}Z"
    all_null = base.model_copy(
        update={
            "alert_id": f"A-{stamp}-91",
            "region": None,
            "suspected_onset": None,
            "hours_since_onset": None,
            "cusum": None,
            "forecast_contributions_m": None,
            "summary": None,
            "audit": None,
        }
    )
    no_sensors = base.model_copy(
        update={"alert_id": f"A-{stamp}-92", "deviating_sensors": (), "summary": None}
    )
    first = base.deviating_sensors[0].sensor
    full = base.model_copy(
        update={
            "alert_id": f"A-{stamp}-93",
            "region": "R-FIXTURE",
            "suspected_onset": base.window_end,
            "hours_since_onset": 0.0,
            "cusum": CusumEvidence(statistic=7.5, threshold=5.0),
            "forecast_contributions_m": {first: {"hour_of_day": 0.4, "flow_p227": -0.2}},
            "summary": "[FIXTURE] every optional field present (invented values)",
            "audit": AuditRef(seq=1, hash="0" * 64),
        }
    )
    return [all_null, no_sensors, full]


def write(out: Path, run_id: str, alerts: list[Alert], status: str, note: str) -> None:
    write_lines(out, run_id, [a.model_dump_json() for a in alerts], status, note)


def write_lines(out: Path, run_id: str, lines: list[str], status: str, note: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "alerts.jsonl").write_text("".join(line + "\n" for line in lines))
    (out / "status.json").write_text(json.dumps(json.loads(status), indent=2) + "\n")
    meta = {"run_id": run_id, "recorded_at": f"{datetime.now(UTC):%Y-%m-%d}", "note": note}
    (out / "run.json").write_text(json.dumps(meta, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", nargs="?", type=Path, default=Path("dashboard/playback"))
    out = ap.parse_args(argv).out

    alerts, status = replay(leak=True)
    if not alerts:
        raise SystemExit("the synthetic step produced no mock alert")
    write(
        out / "fixture-edge-cases",
        "fixture-edge-cases",
        alerts + edge_variants(alerts[0]),
        status,
        "Fixture: mock backend on a synthetic day + hand-made edge-case variants (IDs -91..-93, "
        "invented values). Not a model run.",
    )
    empty, status = replay(leak=False)
    write(
        out / "fixture-empty",
        "fixture-empty",
        empty,
        status,
        "Fixture: mock backend on a synthetic day without a step. Not a model run.",
    )
    broken = alerts[0].model_dump(mode="json", exclude={"models"})
    broken["alert_id"] = alerts[0].alert_id.rsplit("-", 1)[0] + "-99"
    write_lines(
        out / "fixture-malformed",
        "fixture-malformed",
        [alerts[0].model_dump_json(), json.dumps(broken)],
        status,
        "Fixture: second record lacks `models` on purpose (contract mismatch test). "
        "Not a model run.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
