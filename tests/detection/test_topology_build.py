"""From the pipe network to ``regions.<name>.json`` (BE-012g, ADR-011 §2). Development tool,
never imported by the server.

The network and the sensors here are a SYNTHETIC toy; the L-Town files are checked in
``test_topology_files.py``.
"""

import json
from pathlib import Path

import pytest

from sentinel.detection.network import NetworkDocument, PipeNetwork
from sentinel.detection.offline import topology
from sentinel.detection.regions import RegionMapping
from tests.detection.networks import network_document, write_network

# Two zones: a line n1..n5 (n3 has a branch to n7) and the pair n20-n21 fed by the reservoir R1.
# The pump and the valve are links but not pipes, so they do not join the zones.
PIPES = {
    "p1": ["n1", "n2", 400.0],
    "p2": ["n2", "n3", 400.0],
    "p3": ["n3", "n4", 400.0],
    "p4": ["n4", "n5", 400.0],
    "p5": ["n3", "n7", 50.0],
    "p6": ["n20", "n21", 30.0],
    "p7": ["R1", "n20", 10.0],
}
EXCLUDED = [("PUMP_1", "PUMP", "n5", "T1"), ("PRV-1", "PRV", "n5", "n6")]
# n1, n2, n3, n4, n5 are 400 m apart along one line; n20 is alone in the other zone.
SENSORS = ("n1", "n2", "n3", "n4", "n5", "n20")


def toy_network() -> PipeNetwork:
    return PipeNetwork(NetworkDocument.model_validate(network_document(PIPES, EXCLUDED)))


def test_sensors_within_the_diameter_share_a_region_and_zones_never_mix() -> None:
    regions = topology.derive_regions(toy_network(), SENSORS, diameter_m=1000.0)

    # (n1,n2) merge first (400 m, lowest indices), then (n3,n4), then n5 joins them (800 m).
    assert regions == {
        "Z1-R1": ("n1", "n2"),
        "Z1-R2": ("n3", "n4", "n5"),
        "Z2-R1": ("n20",),
    }


def test_a_smaller_diameter_splits_and_a_larger_one_merges() -> None:
    network = toy_network()

    assert topology.derive_regions(network, SENSORS, diameter_m=300.0) == {
        "Z1-R1": ("n1",),
        "Z1-R2": ("n2",),
        "Z1-R3": ("n3",),
        "Z1-R4": ("n4",),
        "Z1-R5": ("n5",),
        "Z2-R1": ("n20",),
    }
    assert topology.derive_regions(network, SENSORS, diameter_m=1600.0) == {
        "Z1-R1": ("n1", "n2", "n3", "n4", "n5"),
        "Z2-R1": ("n20",),
    }


def test_zones_are_numbered_by_sensor_count_not_by_position_in_the_registry() -> None:
    # n20 comes first in the registry but its zone has fewer sensors than the line's.
    regions = topology.derive_regions(toy_network(), ("n20", "n1", "n2"), diameter_m=1000.0)

    assert regions == {"Z1-R1": ("n1", "n2"), "Z2-R1": ("n20",)}


def test_zones_with_as_many_sensors_are_numbered_by_the_first_sensor_in_the_registry() -> None:
    # One sensor each: the zone of n20 comes first because n20 comes first in the registry.
    regions = topology.derive_regions(toy_network(), ("n20", "n1"), diameter_m=1000.0)

    assert regions == {"Z1-R1": ("n20",), "Z2-R1": ("n1",)}


def test_regions_follow_the_registry_order_inside_a_zone() -> None:
    regions = topology.derive_regions(toy_network(), ("n5", "n4", "n3"), diameter_m=1000.0)

    assert regions == {"Z1-R1": ("n5", "n4", "n3")}


def test_a_sensor_that_is_not_a_node_of_the_network_is_refused() -> None:
    with pytest.raises(ValueError, match="n99"):
        topology.derive_regions(toy_network(), ("n1", "n99"), diameter_m=1000.0)


def test_a_sensor_on_a_node_no_pipe_touches_is_refused() -> None:
    # n6 is declared but only the valve touches it: it has no zone.
    with pytest.raises(ValueError, match="n6"):
        topology.derive_regions(toy_network(), ("n1", "n6"), diameter_m=1000.0)


def test_a_non_positive_diameter_is_refused() -> None:
    with pytest.raises(ValueError, match="diameter"):
        topology.derive_regions(toy_network(), SENSORS, diameter_m=0.0)


# -- the command line ------------------------------------------------------------------------


def write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    network = write_network(tmp_path / "l-town.json", PIPES, EXCLUDED)
    registry = tmp_path / "sensors.toy.json"
    registry.write_text(json.dumps({"provisional": False, "pressure": list(SENSORS)}), "utf-8")
    return network, registry


def run(tmp_path: Path, out: Path, *extra: str) -> int:
    network, registry = write_inputs(tmp_path)
    args = ["--network", str(network), "--registry", str(registry)]
    return topology.main([*args, "--out-dir", str(out), "--name", "toy", *extra])


@pytest.fixture
def out(tmp_path: Path) -> Path:
    directory = tmp_path / "config"
    directory.mkdir()
    return directory


def test_the_builder_writes_a_regions_file_the_server_can_load(tmp_path: Path, out: Path) -> None:
    assert run(tmp_path, out) == 0

    mapping = RegionMapping.from_file(out / "regions.toy.json")
    assert mapping.sensors == frozenset(SENSORS)
    assert list(mapping.regions) == ["Z1-R1", "Z1-R2", "Z2-R1"]


def test_building_twice_gives_identical_bytes(tmp_path: Path, out: Path) -> None:
    run(tmp_path, out)
    first = (out / "regions.toy.json").read_bytes()

    assert run(tmp_path, out) == 0
    assert (out / "regions.toy.json").read_bytes() == first


def test_the_regions_file_has_one_region_per_line(tmp_path: Path, out: Path) -> None:
    run(tmp_path, out)

    lines = (out / "regions.toy.json").read_text(encoding="utf-8").splitlines()

    assert [line.strip() for line in lines[2:5]] == [
        '"Z1-R1": ["n1", "n2"],',
        '"Z1-R2": ["n3", "n4", "n5"],',
        '"Z2-R1": ["n20"]',
    ]


def test_the_builder_refuses_to_overwrite_a_different_file(tmp_path: Path, out: Path) -> None:
    run(tmp_path, out)
    (out / "regions.toy.json").write_text('{"regions": {"X": ["n1"]}}', encoding="utf-8")

    with pytest.raises(SystemExit, match=r"regions\.toy\.json"):
        run(tmp_path, out)


def test_check_accepts_the_file_the_rule_gives(tmp_path: Path, out: Path) -> None:
    run(tmp_path, out)

    assert run(tmp_path, out, "--check") == 0


def test_check_refuses_a_different_file_and_writes_nothing(tmp_path: Path, out: Path) -> None:
    other = '{"regions": {"X": ["n1"]}}'
    (out / "regions.toy.json").write_text(other, encoding="utf-8")

    with pytest.raises(SystemExit, match="differs"):
        run(tmp_path, out, "--check")
    assert (out / "regions.toy.json").read_text(encoding="utf-8") == other


def test_check_refuses_a_missing_file(tmp_path: Path, out: Path) -> None:
    with pytest.raises(SystemExit, match="does not exist"):
        run(tmp_path, out, "--check")
    assert not (out / "regions.toy.json").exists()


def test_without_a_network_argument_the_data_directory_layout_is_used(
    tmp_path: Path, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    (data / "network" / "v1").mkdir(parents=True)
    write_network(data / "network" / "v1" / "l-town.json", PIPES, EXCLUDED)
    _, registry = write_inputs(tmp_path)
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(data))

    code = topology.main(["--registry", str(registry), "--out-dir", str(out), "--name", "toy"])

    assert code == 0
    assert RegionMapping.from_file(out / "regions.toy.json").sensors == frozenset(SENSORS)


def test_without_a_network_argument_or_data_directory_it_says_what_is_missing(
    tmp_path: Path, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SENTINEL_DATA_DIR", raising=False)
    _, registry = write_inputs(tmp_path)

    with pytest.raises(SystemExit, match="SENTINEL_DATA_DIR"):
        topology.main(["--registry", str(registry), "--out-dir", str(out)])
