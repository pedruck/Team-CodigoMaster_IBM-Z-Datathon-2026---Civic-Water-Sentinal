"""Replay data integrity: the generator checks its source against the MANIFEST and seals its
output; ``sentinel.audit.replay_check`` compares an audit chain with the replay file."""

import gzip
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel.audit.replay_check import check
from sentinel.audit.replay_check import main as replay_check_main
from sentinel.config import Settings
from sentinel.data import replay_windows
from sentinel.main import build_pipeline, create_app
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import window_payload
from tests.conftest import ts

REGISTRY = Path(__file__).resolve().parents[1] / "config/sensors.battledim.json"


def _data_dir(tmp_path: Path, rows: int = 3) -> Path:
    """A tiny data version: scada_2019.csv.gz with the official columns + its MANIFEST."""
    reg = SensorRegistry.load(REGISTRY)
    columns = (
        [f"Pressure_{s}" for s in reg.pressure]
        + [f"Flow_{s}" for s in reg.flow]
        + [f"Level_{s}" for s in reg.tank_level]
    )
    lines = ["timestamp," + ",".join(columns)]
    for i in range(rows):
        stamp = f"2019-01-01 00:{5 * i:02d}:00"
        lines.append(stamp + "," + ",".join(f"{40 + i}.5" for _ in columns))
    vdir = tmp_path / "data" / "v1"
    vdir.mkdir(parents=True)
    scada = vdir / "scada_2019.csv.gz"
    scada.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode(), mtime=0))
    digest = hashlib.sha256(scada.read_bytes()).hexdigest()
    (vdir / "MANIFEST.json").write_text(
        json.dumps({"files": {"scada_2019.csv.gz": {"sha256": digest}}}), encoding="utf-8"
    )
    return tmp_path / "data"


def _generate(data: Path) -> Path:
    replay_windows.main(["--data-dir", str(data), "--registry", str(REGISTRY)])
    return data / "replay" / "v1" / "windows_2019.jsonl"


def test_generator_seals_its_output(tmp_path: Path) -> None:
    out = _generate(_data_dir(tmp_path))
    sidecar = json.loads((out.parent / "windows_2019.jsonl.manifest.json").read_text())

    assert sidecar["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert sidecar["windows"] == 3 == len(out.read_text().splitlines())
    assert (sidecar["first"], sidecar["last"]) == ("2019-01-01T00:00:00Z", "2019-01-01T00:10:00Z")
    scada = tmp_path / "data/v1/scada_2019.csv.gz"
    assert sidecar["source"]["sha256"] == hashlib.sha256(scada.read_bytes()).hexdigest()
    assert sidecar["registry"]["sha256"] == hashlib.sha256(REGISTRY.read_bytes()).hexdigest()


def test_generator_refuses_a_source_that_differs_from_the_manifest(tmp_path: Path) -> None:
    data = _data_dir(tmp_path)
    scada = data / "v1/scada_2019.csv.gz"
    scada.write_bytes(scada.read_bytes() + b"\0")
    with pytest.raises(SystemExit, match="difere do MANIFEST"):
        _generate(data)
    assert not (data / "replay").exists()


def test_generator_refuses_an_unsealed_version(tmp_path: Path) -> None:
    data = _data_dir(tmp_path)
    (data / "v1/MANIFEST.json").unlink()
    with pytest.raises(SystemExit, match="não está selada"):
        _generate(data)


# ------------------------------------------------------------------ audit chain vs file


def _replay_into_chain(
    tmp_path: Path, settings: Settings, registry: SensorRegistry, n: int
) -> Path:
    s = replace(settings, audit_sink="chain", audit_run="check", audit_dir=tmp_path)
    client = TestClient(create_app(s, build_pipeline(s, registry=registry)))
    windows = tmp_path / "windows.jsonl"
    lines = [json.dumps(window_payload(ts(i), registry)) for i in range(n)]
    windows.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines[: n - 1]:  # the last window is never sent: a partial replay
        assert client.post("/v1/windows", json=json.loads(line)).status_code == 201
    return windows


def test_chain_matches_the_replay_file(
    tmp_path: Path, settings: Settings, registry: SensorRegistry
) -> None:
    windows = _replay_into_chain(tmp_path, settings, registry, 5)
    result = check(tmp_path / "check.jsonl", windows)
    assert result.ok and result.chain_ok
    assert (result.windows_in_file, result.recorded_windows, result.matched) == (5, 4, 4)
    assert result.not_recorded == 1 and result.mismatched == [] and result.not_in_file == []
    assert replay_check_main([str(tmp_path / "check.jsonl"), str(windows)]) == 0


def test_chain_differs_from_an_edited_file(
    tmp_path: Path,
    settings: Settings,
    registry: SensorRegistry,
    capsys: pytest.CaptureFixture[str],
) -> None:
    windows = _replay_into_chain(tmp_path, settings, registry, 5)
    rows = [json.loads(line) for line in windows.read_text().splitlines()]
    rows[2]["pressures"]["P1"] += 0.001  # one reading changed after the replay
    windows.write_text("\n".join(json.dumps(r) for r in rows[1:]) + "\n")  # window 0 dropped

    result = check(tmp_path / "check.jsonl", windows)

    assert not result.ok
    assert result.mismatched == ["2019-01-01T00:10:00Z"]
    assert result.not_in_file == ["2019-01-01T00:00:00Z"]
    assert replay_check_main([str(tmp_path / "check.jsonl"), str(windows)]) == 1
    assert json.loads(capsys.readouterr().out)["mismatched_count"] == 1
