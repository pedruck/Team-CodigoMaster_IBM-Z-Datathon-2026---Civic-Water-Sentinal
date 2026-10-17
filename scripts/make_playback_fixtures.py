"""Write the dashboard's playback fixture runs (``dashboard/playback/``).

Both runs come from the backend itself, in process, with the **mocks** over one synthetic day
(invented signal, official sensor IDs):

- ``fixture-empty``: no injected step → no alert; real status.
- ``fixture-edge-cases``: a pressure step on 3 sensors → the mock alert(s), plus hand-made
  variants of that alert covering the contract edges (every nullable field null, empty
  deviating-sensor list, every nullable field present). Variant values are invented for UI
  testing; they stay ``is_mock=true`` and the run note says so.
- ``fixture-cusum``: the **CUSUM detector** (ADR-007) with an invented ``synthetic`` calibration
  (sigma 0.1 m, k 0.5, h 8, N 3, c 2, h_close 4 → ``is_mock=true``) and an invented two-region
  mapping (``R-SYN-A`` / ``R-SYN-B``), over a day where 3 sensors of ``R-SYN-A`` ramp down
  0.2 m per window from 16:40. Exercises the fields the mock never fills: ``region``,
  ``cusum``, ``since``, a ``suspected_onset`` before the alert. Not a calibration result.
- ``fixture-malformed``: one valid mock alert and one record without ``models`` (contract
  violation on purpose) → the dashboard must show "contract mismatch".

Usage: ``uv run python scripts/make_playback_fixtures.py [out_dir]``.
"""

import argparse
import json
import random
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from sentinel.config import Settings
from sentinel.detection.calibration import CalibrationArtefact, write_artefact
from sentinel.main import build_pipeline, create_app
from sentinel.schemas.alerts import Alert, AuditRef
from sentinel.schemas.detections import CusumEvidence
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import generate, window_payload

DAY = datetime(2019, 3, 14, tzinfo=UTC)
STEP = timedelta(minutes=5)
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


def replay_cusum() -> tuple[list[Alert], str]:
    registry = SensorRegistry.load(OFFICIAL_REGISTRY)
    pressure = registry.pressure
    half = len(pressure) // 2
    with tempfile.TemporaryDirectory() as tmp:
        calibration = Path(tmp) / "calibration.synthetic.json"
        artefact = CalibrationArtefact.model_validate(
            {
                "provenance": "synthetic",  # invented numbers: forces is_mock=true (ADR-008 §B)
                "forecaster": {"name": "mock-persistence", "version": "0.1.0"},
                "residuals_sha256": "0" * 64,
                "folds_sha256": "0" * 64,
                "sigma_m": dict.fromkeys(pressure, 0.1),
                "k": 0.5,
                "h": 8.0,
                "n_min": 3,
                "c": 2.0,
                "h_close": 4.0,
            }
        )
        write_artefact(artefact, calibration)
        regions = Path(tmp) / "regions.synthetic.json"
        regions.write_text(
            json.dumps(
                {"regions": {"R-SYN-A": list(pressure[:half]), "R-SYN-B": list(pressure[half:])}}
            )
        )
        settings = Settings(
            audit_sink="memory",
            sensor_registry_path=OFFICIAL_REGISTRY,
            detector="cusum",
            detector_calibration_path=calibration,
            detector_regions_path=regions,
        )
        client = TestClient(create_app(settings, build_pipeline(settings)))
        rng = random.Random(7)
        ramp_at, leaking = 200, pressure[:3]
        for i in range(288):
            drop = 0.2 * (i - ramp_at + 1) if i >= ramp_at else 0.0
            window = window_payload(
                DAY + i * STEP, registry, drop_m=drop, drop_sensors=leaking, rng=rng
            )
            client.post("/v1/windows", json=window)
        page = client.get("/v1/alerts", params={"limit": 500}).json()
        alerts = [Alert.model_validate(a) for a in reversed(page["items"])]
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
    cusum_alerts, cusum_status = replay_cusum()
    if not cusum_alerts:
        raise SystemExit("the CUSUM ramp produced no alert")
    write(
        out / "fixture-cusum",
        "fixture-cusum",
        cusum_alerts,
        cusum_status,
        "Fixture: CUSUM detector with an INVENTED synthetic calibration and region mapping, mock "
        "forecaster, synthetic day with a ramp on 3 sensors. Not a calibration or model result.",
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
