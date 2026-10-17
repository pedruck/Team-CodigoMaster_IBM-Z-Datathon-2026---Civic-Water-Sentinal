"""Layer-2 evaluation run (ADR-010): check labels → replay through the API → report.

    python -m sentinel.eval --windows windows_2019.jsonl --api http://127.0.0.1:8000 \\
        --labels eval/v1/leaks_2019.csv --out runs/<run-id> [--registry ...] [--calibration ...]

The API must be a fresh instance (nothing replayed yet) with the models under test. Writes the
run artefacts (``sentinel.eval.run``), ``report.md`` and ``manifest.json`` in ``--out``.
"""

import argparse
import hashlib
import json
import platform
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentinel.eval.labels import check_official_labels, read_labels
from sentinel.eval.metrics import evaluate_run
from sentinel.eval.report import PROTOCOL, Manifest, render_report
from sentinel.eval.run import record_run
from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import StatusResponse

INTERVAL = timedelta(minutes=5)
REPO = Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()


def calibration_id(detector: str, version: str, calibration: Path | None) -> str | None:
    """sha256 of the calibration artefact the API runs with (ADR-010 §8). For the CUSUM it is
    required and must match the ``+<8 hex>`` suffix of the detector version (ADR-008 §B)."""
    if detector != "cusum":
        return _sha256(calibration) if calibration else None
    if calibration is None:
        raise ValueError("the CUSUM detector needs --calibration (the artefact the API runs with)")
    digest = _sha256(calibration)
    if not version.endswith(f"+{digest[:8]}"):
        raise ValueError(f"--calibration sha256 {digest[:8]} does not match the detector {version}")
    return digest


def _first_window(path: Path) -> datetime:
    with path.open() as lines:
        first = next(line for line in lines if line.strip())
    return datetime.fromisoformat(json.loads(first)["timestamp"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--windows", type=Path, required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--registry", type=Path, default=REPO / "config/sensors.battledim.json")
    ap.add_argument(
        "--calibration", type=Path, help="calibration artefact the API was started with"
    )
    ap.add_argument("--run-id", default=f"eval-{datetime.now(UTC):%Y%m%dT%H%MZ}")
    args = ap.parse_args(argv)

    labels = read_labels(args.labels)
    check_official_labels(labels)  # before posting anything (ADR-010 §10)

    summary = record_run(args.windows, args.api, args.out, run_id=args.run_id)
    alerts = [
        Alert.model_validate_json(line)
        for line in (args.out / "alerts.jsonl").read_text().splitlines()
        if line.strip()
    ]
    status = StatusResponse.model_validate_json((args.out / "status.json").read_text())
    year_start = datetime(_first_window(args.windows).year, 1, 1, tzinfo=UTC)
    evaluation = evaluate_run(
        alerts, labels, period=summary.accepted * INTERVAL, year_start=year_start
    )
    manifest = Manifest(
        run_id=args.run_id,
        commit=_git("rev-parse", "HEAD"),
        dirty=bool(_git("status", "--porcelain", "--untracked-files=no")),
        forecaster=status.forecaster,
        detector=status.detector,
        registry_provisional=bool(status.sensor_registry.get("provisional", True)),
        registry_sha256=_sha256(args.registry),
        lock_sha256=_sha256(REPO / "uv.lock"),
        calibration_id=calibration_id(
            status.detector.name, status.detector.version, args.calibration
        ),
        data_sha256={p.name: _sha256(p) for p in (args.windows, args.labels)},
        architecture=f"evaluator {platform.machine()}",
        python=platform.python_version(),
        windows=summary.windows,
        rejected_by_code=summary.rejected_by_code,
        missing_alerts=summary.missing_alerts,
    )
    (args.out / "report.md").write_text(render_report(evaluation, manifest))
    record = asdict(manifest) | {
        "forecaster": manifest.forecaster.model_dump(),
        "detector": manifest.detector.model_dump(),
        "is_mock": manifest.is_mock,
        "protocol": PROTOCOL,
        "year_start": year_start.isoformat(),
        "rejected": summary.rejected,
        "alerts": summary.alerts,
    }
    (args.out / "manifest.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
    print(json.dumps({k: record[k] for k in ("run_id", "windows", "rejected", "alerts")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
