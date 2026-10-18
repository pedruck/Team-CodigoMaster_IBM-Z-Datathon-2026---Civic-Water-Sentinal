"""Candidate pipe of an alert (BE-012i, Q-DET-07, ADR-011 §3).

Seam A: the detector alone over a toy line network whose nodes are the invented sensors
``P1..P4``. Seam B: ``POST /v1/windows`` -> ``GET /v1/alerts`` over the OFFICIAL registry with the
committed L-Town regions, a synthetic signal and an invented calibration, on two networks: a chain
through the 33 pressure sensors (everywhere) and the data owner's real ``l-town.json`` (when
``$SENTINEL_DATA_DIR/network/v1/l-town.json`` exists, as on the VM).

Everything numeric here is made up for the tests; the checks on the real network say where the
candidate is NOT (outside the sensors' zone) and that it is a pipe of the network, never that it
is the leak. Nothing here is evidence of leak localisation.
"""

import itertools
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.audit.sinks import NullAuditSink
from sentinel.config import Settings
from sentinel.detection.cusum import CusumDetector
from sentinel.detection.network import PipeNetwork
from sentinel.detection.regions import RegionMapping
from sentinel.main import build_pipeline, create_app
from sentinel.schemas.alerts import Alert
from sentinel.schemas.detections import DetectionResult
from sentinel.sensors import SensorRegistry
from sentinel.synthetic import window_payload
from tests.conftest import INTERVAL, ts
from tests.detection.artefacts import make_artefact
from tests.detection.networks import write_network
from tests.detection.test_end_to_end import CALIBRATION, STEP_M, ProfileForecaster

# Seam A: P1 - P2 - P3 - P4 on a line, 200 m per pipe; sigma 0.1 m, k = 0.5, h = 8, N = 1, c = 2.
# A 0.5 m drop gives z = 5 and S grows by 4.5 per window (4.5, 9.0): the alert opens on the
# second stepped window. A 0.2 m drop gives z = 2 and S grows by 1.5 (1.5, 3.0): below h.
SENSORS = ("P1", "P2", "P3", "P4")
LINE: dict[str, list[Any]] = {
    "q1": ["P1", "P2", 200.0],
    "q2": ["P2", "P3", 200.0],
    "q3": ["P3", "P4", 200.0],
}


def line_network(tmp_path: Path, pipes: Mapping[str, list[Any]] = LINE) -> PipeNetwork:
    return PipeNetwork.from_file(write_network(tmp_path / "network.line.json", pipes))


def detector(network: PipeNetwork | None) -> CusumDetector:
    return CusumDetector(
        make_artefact(dict.fromkeys(SENSORS, 0.1)), interval=INTERVAL, network=network
    )


def windows(
    det: CusumDetector, drops: Mapping[str, float], count: int = 2
) -> list[DetectionResult]:
    """``count`` consecutive windows; each sensor of ``drops`` reads that many metres low."""
    expected = dict.fromkeys(SENSORS, 50.0)
    observed = {s: 50.0 - drops.get(s, 0.0) for s in SENSORS}
    return [det.evaluate(observed, expected, ts(i)) for i in range(count)]


def candidate(det: CusumDetector, drops: Mapping[str, float]) -> str | None:
    (alert,) = windows(det, drops)[-1].alerts
    return alert.candidate_pipe


def test_without_a_network_the_alert_has_no_candidate_pipe() -> None:
    assert candidate(detector(None), {"P1": 0.5}) is None


def test_a_drop_at_one_end_of_the_line_gives_the_pipe_at_that_end(tmp_path: Path) -> None:
    network = line_network(tmp_path)

    assert candidate(detector(network), {"P1": 0.5}) == "q1"
    assert candidate(detector(network), {"P4": 0.5}) == "q3"


def test_equal_drops_at_both_ends_give_the_pipe_in_the_middle(tmp_path: Path) -> None:
    assert candidate(detector(line_network(tmp_path)), {"P1": 0.5, "P4": 0.5}) == "q2"


def test_a_sensor_still_below_the_threshold_does_not_pull_the_candidate(tmp_path: Path) -> None:
    # P4 is displaced too (S = 3.0 < h = 8) but the alert's evidence is P1 alone.
    assert candidate(detector(line_network(tmp_path)), {"P1": 0.5, "P4": 0.2}) == "q1"


def test_the_first_window_of_the_step_opens_nothing_and_so_names_no_pipe(tmp_path: Path) -> None:
    first = windows(detector(line_network(tmp_path)), {"P1": 0.5}, count=1)[-1]

    assert first.alerts == ()


def test_diagnostics_carry_the_network_id_only_with_a_network(tmp_path: Path) -> None:
    network = line_network(tmp_path)

    with_network = windows(detector(network), {}, count=1)[0].diagnostics
    without = windows(detector(None), {}, count=1)[0].diagnostics

    assert with_network["network_id"] == network.network_id
    assert "network_id" not in without


def test_a_network_that_lacks_a_sensor_node_is_refused(tmp_path: Path) -> None:
    network = line_network(tmp_path, {"q1": ["P1", "P2", 200.0], "q2": ["P2", "P3", 200.0]})

    with pytest.raises(ValueError, match="P4"):
        detector(network)


# -- Seam B: the official registry with the committed L-Town regions, over HTTP ----------------

CONFIG = Path(__file__).resolve().parents[2] / "config"
DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/nao-existe")).expanduser()
L_TOWN = DATA_DIR / "network" / "v1" / "l-town.json"
needs_network = pytest.mark.skipif(
    not L_TOWN.exists(), reason="$SENTINEL_DATA_DIR/network/v1/l-town.json unavailable"
)
OFFICIAL = SensorRegistry.load(CONFIG / "sensors.battledim.json")
L_TOWN_REGIONS = RegionMapping.from_file(CONFIG / "regions.battledim.json")


def chain_over_the_sensors(path: Path) -> PipeNetwork:
    """100 m pipes ``c1, c2, ...`` through the pressure sensors in registry order."""
    pipes = {
        f"c{i}": [a, b, 100.0] for i, (a, b) in enumerate(itertools.pairwise(OFFICIAL.pressure), 1)
    }
    return PipeNetwork.from_file(write_network(path, pipes))


@pytest.fixture(params=["chain", pytest.param("l-town", marks=needs_network)])
def l_town_network(request: pytest.FixtureRequest, tmp_path: Path) -> PipeNetwork:
    if request.param == "chain":
        return chain_over_the_sensors(tmp_path / "network.chain.json")
    return PipeNetwork.from_file(L_TOWN)


def replay_l_town(dropped: Sequence[str], network: PipeNetwork) -> list[dict[str, Any]]:
    """A synthetic day in which ``dropped`` read STEP_M low from window 200 on, through the API
    with the L-Town registry and regions and the given network. Returns ``GET /v1/alerts``."""
    det = CusumDetector(
        make_artefact(dict.fromkeys(OFFICIAL.pressure, 0.1), **{**CALIBRATION, "n_min": 1}),
        interval=INTERVAL,
        regions=L_TOWN_REGIONS,
        network=network,
    )
    settings = Settings()
    pipeline = build_pipeline(
        settings,
        registry=OFFICIAL,
        forecaster=ProfileForecaster(OFFICIAL),
        detector=det,
        audit_sink=NullAuditSink(),
    )
    with TestClient(create_app(settings, pipeline)) as client:
        for i in range(288):
            drop = STEP_M if i >= 200 else 0.0
            window = window_payload(ts(i), OFFICIAL, drop_m=drop, drop_sensors=dropped)
            assert client.post("/v1/windows", json=window).status_code == 201
        items: list[dict[str, Any]] = client.get("/v1/alerts").json()["items"]
        return items


def region_sensors(region: str) -> tuple[str, ...]:
    return L_TOWN_REGIONS.regions[region]


def test_a_lone_sensor_region_names_a_pipe_that_touches_the_sensor(
    l_town_network: PipeNetwork,
) -> None:
    (alert,) = replay_l_town(region_sensors("Z3-R1"), l_town_network)  # n215

    assert alert["region"] == "Z3-R1"
    assert "n215" in l_town_network.ends(alert["candidate_pipe"])


@needs_network
def test_the_candidate_of_a_region_lies_in_the_zone_of_its_sensors() -> None:
    network = PipeNetwork.from_file(L_TOWN)

    (alert,) = replay_l_town(region_sensors("Z2-R1"), network)  # n1, n4, n31 under the tank

    assert alert["region"] == "Z2-R1"
    assert network.zone_of_pipe(alert["candidate_pipe"]) == network.zone_of("n1")


def test_the_alert_payload_is_version_1_1_and_round_trips_the_contract(
    l_town_network: PipeNetwork,
) -> None:
    (alert,) = replay_l_town(region_sensors("Z3-R1"), l_town_network)

    assert alert["schema_version"] == "1.1"
    assert Alert.model_validate(alert).candidate_pipe == alert["candidate_pipe"]


def test_a_version_1_0_alert_without_the_field_is_still_accepted(
    l_town_network: PipeNetwork,
) -> None:
    (alert,) = replay_l_town(region_sensors("Z3-R1"), l_town_network)
    old = {k: v for k, v in alert.items() if k != "candidate_pipe"} | {"schema_version": "1.0"}

    parsed = Alert.model_validate(old)

    assert parsed.candidate_pipe is None
    assert parsed.schema_version == "1.0"
