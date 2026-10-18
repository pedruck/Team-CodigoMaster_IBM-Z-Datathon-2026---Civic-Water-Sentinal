"""``SENTINEL_DETECTOR_NETWORK``: the server starts the CUSUM detector with the pipe network
(BE-012g / BE-012i, ADR-011).

Two kinds of file. The synthetic ones (a chain of pipes over the placeholder sensors ``P01..P33``)
exercise the startup checks and the candidate pipe through HTTP. The data owner's real
``l-town.json`` (``$SENTINEL_DATA_DIR/network/v1/``, as on the VM) is started once with the official
registry and the committed regions. Calibrations are invented; none of this says anything
about detecting or locating leaks.
"""

import itertools
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel.config import Settings
from sentinel.detection.network import PipeNetwork
from sentinel.main import create_app
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import window_payload
from tests.conftest import ts
from tests.detection.networks import write_network
from tests.test_detector_startup import (
    RAMP_AT,
    RAMP_STEP_M,
    RAMPED,
    REGISTRY,
    WINDOWS,
    cusum_env,
    refused,
    replay_ramp,
    write_calibration,
)

CONFIG = Path(__file__).resolve().parents[1] / "config"
DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/nao-existe")).expanduser()
L_TOWN = DATA_DIR / "network" / "v1" / "l-town.json"
needs_network = pytest.mark.skipif(
    not L_TOWN.exists(), reason="$SENTINEL_DATA_DIR/network/v1/l-town.json unavailable"
)


def write_chain(path: Path, nodes: tuple[str, ...]) -> Path:
    """A chain of 100 m pipes ``q1, q2, ...`` through ``nodes`` in order."""
    pipes = {f"q{i}": [a, b, 100.0] for i, (a, b) in enumerate(itertools.pairwise(nodes), 1)}
    return write_network(path, pipes)


def with_network(env: dict[str, str], network: Path) -> dict[str, str]:
    return {**env, "SENTINEL_DETECTOR_NETWORK": str(network)}


def test_a_ramp_alert_names_a_pipe_among_the_ramped_sensors(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    network = write_chain(tmp_path / "network.chain.json", REGISTRY.pressure)

    run = replay_ramp(with_network(cusum_env(calibration), network))

    (alert,) = run.alerts
    # RAMPED are three neighbours on the chain with equal evidence: the candidate sits between
    # them, on one of the two pipes that join them (ties go to the first pipe of the file).
    first = REGISTRY.pressure.index(RAMPED[0]) + 1
    assert alert["candidate_pipe"] in {f"q{first}", f"q{first + 1}"}


def test_without_a_network_the_ramp_alert_has_no_candidate_pipe(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")

    (alert,) = replay_ramp(cusum_env(calibration)).alerts

    assert alert["candidate_pipe"] is None


def test_a_network_without_a_registry_sensor_is_refused(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    network = write_chain(tmp_path / "network.chain.json", REGISTRY.pressure[:-1])

    refused(with_network(cusum_env(calibration), network), rf"lacks nodes.*{REGISTRY.pressure[-1]}")


def test_a_missing_network_file_is_refused_naming_the_path(tmp_path: Path) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    missing = tmp_path / "no-such-network.json"

    refused(with_network(cusum_env(calibration), missing), re.escape(str(missing)))


@pytest.mark.parametrize("content", ["not json {", "{}", '{"pipes": {}}'])
def test_an_invalid_network_file_is_refused_naming_the_path(tmp_path: Path, content: str) -> None:
    calibration = write_calibration(tmp_path / "calibration.synthetic.json")
    network = tmp_path / "network.json"
    network.write_text(content, encoding="utf-8")

    refused(with_network(cusum_env(calibration), network), re.escape(str(network)))


@needs_network
def test_the_server_started_from_the_environment_localises_a_ramp_in_the_tank_zone(
    tmp_path: Path,
) -> None:
    """What the VM runs: the official registry, the committed L-Town regions and the data owner's
    ``network/v1/l-town.json``, all given by environment variable."""
    official = SensorRegistry.load(CONFIG / "sensors.battledim.json")
    calibration = write_calibration(
        tmp_path / "calibration.synthetic.json", sensors=official.pressure
    )
    env = {
        **cusum_env(calibration),
        "SENTINEL_SENSOR_REGISTRY": str(CONFIG / "sensors.battledim.json"),
        "SENTINEL_DETECTOR_REGIONS": str(CONFIG / "regions.battledim.json"),
        "SENTINEL_DETECTOR_NETWORK": str(L_TOWN),
    }
    tank_zone = ("n1", "n4", "n31")  # the zone behind the pump; exactly N = 3 sensors

    with TestClient(create_app(Settings.from_env(env))) as client:
        status = client.get("/v1/status").json()
        for i in range(WINDOWS):
            ramp = RAMP_STEP_M * max(0, i - RAMP_AT + 1)
            window = window_payload(ts(i), official, drop_m=ramp, drop_sensors=tank_zone)
            assert client.post("/v1/windows", json=window).status_code == 201
        (alert,) = client.get("/v1/alerts").json()["items"]

    network = PipeNetwork.from_file(L_TOWN)
    assert status["sensor_registry"]["provisional"] is False
    assert alert["region"] == "Z2-R1"
    assert network.zone_of_pipe(alert["candidate_pipe"]) == network.zone_of("n1")
