"""The L-Town sensor -> region file ``config/regions.battledim.json`` and the data owner's pipe
network ``l-town.json`` (BE-012g, ADR-011).

The checks on the committed regions file run everywhere. The ones that read the network need
``$SENTINEL_DATA_DIR/network/v1/l-town.json`` (outside git; the layout of the data directory on the
LinuxONE VM) and are skipped without it, like the data tests of P1. Run on the VM, they are the
check that the file there is the one the regions were built from.
"""

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

from sentinel.detection.network import PipeNetwork
from sentinel.detection.offline import topology
from sentinel.detection.regions import RegionMapping
from sentinel.sensors import SensorRegistry

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
REGISTRY = SensorRegistry.load(CONFIG / "sensors.battledim.json")
DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/nao-existe")).expanduser()
L_TOWN = DATA_DIR / "network" / "v1" / "l-town.json"
needs_network = pytest.mark.skipif(
    not L_TOWN.exists(), reason="$SENTINEL_DATA_DIR/network/v1/l-town.json unavailable"
)

# Pins: the mapping is fixed before any evaluation (ADR-011 §2, ADR-008 §G). A change to the regions
# file or to the network it was built from is a new mapping: new file name, new ADR entry, and
# these values updated on purpose.
REGIONS_SHA256 = "ca365b7074d2bb3df2cec38d69768850ca1d37b9a717db62a9f95a71a25fe57d"
L_TOWN_JSON_SHA256 = "ff772dc1b68c59c05dc3a383fe739a624c054a7fd7c7544bc3b8c6bcbbf49b59"
L_TOWN_INP_SHA256 = "a7551b86745f4cc3433c78e60023077fa1386947d4d35372ffd3a0405622b436"


@pytest.fixture(scope="module")
def mapping() -> RegionMapping:
    return RegionMapping.from_file(CONFIG / "regions.battledim.json")


@pytest.fixture(scope="module")
def network() -> PipeNetwork:
    return PipeNetwork.from_file(L_TOWN)


def test_the_committed_regions_file_is_the_one_that_was_fixed() -> None:
    digest = hashlib.sha256((CONFIG / "regions.battledim.json").read_bytes()).hexdigest()

    assert digest == REGIONS_SHA256


def test_the_regions_cover_exactly_the_pressure_sensors_of_the_official_registry(
    mapping: RegionMapping,
) -> None:
    assert mapping.sensors == frozenset(REGISTRY.pressure)


@needs_network
def test_the_network_file_is_the_one_the_regions_were_built_from() -> None:
    assert hashlib.sha256(L_TOWN.read_bytes()).hexdigest() == L_TOWN_JSON_SHA256


@needs_network
def test_the_network_file_declares_the_model_it_came_from(network: PipeNetwork) -> None:
    assert (network.source.file, network.source.sha256) == ("L-TOWN.inp", L_TOWN_INP_SHA256)
    assert network.network_id == L_TOWN_JSON_SHA256


@needs_network
def test_every_pressure_sensor_is_a_node_of_the_network(network: PipeNetwork) -> None:
    assert set(REGISTRY.pressure) <= network.nodes


@needs_network
def test_no_region_crosses_a_valve_or_the_pump(
    network: PipeNetwork, mapping: RegionMapping
) -> None:
    for region, sensors in mapping.regions.items():
        assert len({network.zone_of(s) for s in sensors}) == 1, region


@needs_network
def test_the_pump_and_the_valves_are_the_only_links_that_are_not_pipes(
    network: PipeNetwork,
) -> None:
    assert set(network.excluded_links) == {"PUMP_1", "PRV-1", "PRV-2", "PRV-3"}
    assert len(network.pipes) == 905


@needs_network
def test_the_tank_zone_and_the_prv_zone_are_not_part_of_the_main_zone(network: PipeNetwork) -> None:
    # The pump (n54 -> T1) and the PRVs are the only way into the zones of n1/n4/n31 and n215.
    assert network.zone_of("n1") == network.zone_of("n4") == network.zone_of("n31")
    assert network.zone_of("n1") != network.zone_of("n54")
    assert network.zone_of("n215") not in {network.zone_of("n1"), network.zone_of("n54")}


@needs_network
def test_the_zones_of_the_data_owner_are_the_groups_of_nodes_the_pipes_join(
    network: PipeNetwork,
) -> None:
    # An independent cross-check: l-town.json labels every node with a zone (Z1..Z5) computed by
    # the data owner; the detector computes its own from the pipes. Same partition.
    labelled = json.loads(L_TOWN.read_text(encoding="utf-8"))["nodes"]
    theirs: dict[str, set[str]] = defaultdict(set)
    for node in labelled:
        theirs[node["zone"]].add(node["id"])
    ours: dict[int, set[str]] = defaultdict(set)
    for node in network.nodes:
        ours[network.zone_of(node)].add(node)

    assert {frozenset(g) for g in theirs.values()} == {frozenset(g) for g in ours.values()}


@needs_network
def test_rebuilding_the_regions_from_the_network_gives_the_committed_file() -> None:
    args = ["--network", str(L_TOWN), "--registry", str(CONFIG / "sensors.battledim.json")]

    assert topology.main([*args, "--out-dir", str(CONFIG), "--check"]) == 0
