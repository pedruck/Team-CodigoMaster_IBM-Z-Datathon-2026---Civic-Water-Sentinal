"""Pipe network of the detector (BE-012g / BE-012i, ADR-011): distances along pipes, hydraulic
zones and the candidate pipe of an alert.

The network here is a SYNTHETIC toy graph written to ``tmp_path``; the L-Town file is checked in
``test_topology_files.py``. Lengths are in metres::

    n1 --p1(100)-- n2 --p2(100)-- n3 --p3(100)-- n4        n8 --p6(10)-- n9
                    |
                  p4(50)
                    |
                   n5

``n8``-``n9`` is a second zone (no pipe connects it to the first one).
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from sentinel.detection.network import PipeNetwork
from tests.detection.networks import network_document, write_network

PIPES: dict[str, list[Any]] = {
    "p1": ["n1", "n2", 100.0],
    "p2": ["n2", "n3", 100.0],
    "p3": ["n3", "n4", 100.0],
    "p4": ["n2", "n5", 50.0],
    "p6": ["n8", "n9", 10.0],
}


def write_toy(
    tmp_path: Path,
    pipes: Mapping[str, list[Any]] = PIPES,
    excluded: Sequence[tuple[str, str, str, str]] = (),
    **overrides: Any,
) -> Path:
    return write_network(tmp_path / "network.toy.json", pipes, excluded, **overrides)


@pytest.fixture
def network(tmp_path: Path) -> PipeNetwork:
    return PipeNetwork.from_file(write_toy(tmp_path))


def test_distance_follows_the_pipes_and_is_infinite_between_zones(network: PipeNetwork) -> None:
    assert network.distance("n1", "n4") == 300.0
    assert network.distance("n5", "n4") == 250.0
    assert network.distance("n4", "n4") == 0.0
    assert network.distance("n1", "n9") == math.inf


def test_zones_are_the_groups_of_nodes_joined_by_pipes(network: PipeNetwork) -> None:
    assert network.zone_of("n1") == network.zone_of("n5")
    assert network.zone_of("n8") == network.zone_of("n9")
    assert network.zone_of("n1") != network.zone_of("n8")


def test_pipe_distance_is_measured_between_pipe_midpoints(network: PipeNetwork) -> None:
    assert network.pipe_distance("p1", "p1") == 0.0
    assert network.pipe_distance("p1", "p2") == 100.0  # 50 + 50
    assert network.pipe_distance("p1", "p3") == 200.0  # 50 + 100 + 50
    assert network.pipe_distance("p1", "p6") == math.inf


def test_one_deviating_sensor_gives_a_pipe_next_to_it(network: PipeNetwork) -> None:
    # n1 touches only p1; n2 touches p1, p2 and p4 and p4 is the shortest (midpoint at 25 m).
    assert network.candidate_pipe({"n1": 4.0}) == "p1"
    assert network.candidate_pipe({"n2": 4.0}) == "p4"


def test_two_equal_sensors_give_the_pipe_in_the_middle(network: PipeNetwork) -> None:
    # n1 and n4 are 300 m apart; p2's midpoint is 150 m from both (a sum of squared distances
    # of 45 000, against 65 000 for p1 and p3).
    assert network.candidate_pipe({"n1": 8.0, "n4": 8.0}) == "p2"


def test_the_candidate_moves_towards_the_stronger_evidence(network: PipeNetwork) -> None:
    assert network.candidate_pipe({"n1": 24.0, "n3": 8.0}) == "p1"
    # n3 touches p2 and p3; p2 also leans towards the weaker n1, so it wins over p3.
    assert network.candidate_pipe({"n1": 8.0, "n3": 24.0}) == "p2"


def test_only_the_zone_with_the_most_evidence_counts(network: PipeNetwork) -> None:
    assert network.candidate_pipe({"n1": 9.0, "n9": 50.0}) == "p6"
    assert network.candidate_pipe({"n1": 50.0, "n9": 9.0}) == "p1"


def test_equal_evidence_in_two_zones_goes_to_the_zone_that_comes_first_in_the_file(
    network: PipeNetwork,
) -> None:
    assert network.candidate_pipe({"n1": 8.0, "n9": 8.0}) == "p1"
    assert network.zone_of_pipe("p6") != network.zone_of_pipe("p1")


def test_a_tie_goes_to_the_first_pipe_of_the_file(network: PipeNetwork) -> None:
    # n1 and n3 are both 100 m from n2: p1 and p2 are equally good, p1 comes first in the file.
    assert network.candidate_pipe({"n1": 8.0, "n3": 8.0}) == "p1"


@pytest.mark.parametrize(
    "weights",
    [{}, {"n99": 8.0}, {"n1": 0.0}, {"n1": -1.0}, {"n1": math.nan}, {"n1": math.inf}],
)
def test_candidate_pipe_refuses_empty_unknown_or_non_positive_evidence(
    network: PipeNetwork, weights: dict[str, float]
) -> None:
    with pytest.raises(ValueError):
        network.candidate_pipe(weights)


def test_network_id_is_the_sha256_of_the_file(tmp_path: Path) -> None:
    path = write_toy(tmp_path)

    assert PipeNetwork.from_file(path).network_id == hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_network_knows_its_pipes_nodes_and_source(network: PipeNetwork) -> None:
    assert network.pipes == ("p1", "p2", "p3", "p4", "p6")
    assert network.nodes == frozenset({"n1", "n2", "n3", "n4", "n5", "n8", "n9"})
    assert (network.source.file, network.source.sha256) == ("toy.inp", "0" * 64)


def test_pumps_and_valves_are_kept_apart_from_the_pipes(tmp_path: Path) -> None:
    excluded = [("PRV-1", "PRV", "n4", "n8"), ("PUMP_1", "PUMP", "n3", "n9")]

    network = PipeNetwork.from_file(write_toy(tmp_path, excluded=excluded))

    assert network.distance("n4", "n8") == math.inf  # a valve is not a pipe of the graph
    assert network.excluded_links == {
        "PRV-1": ("PRV", "n4", "n8"),
        "PUMP_1": ("PUMP", "n3", "n9"),
    }
    assert network.pipes == ("p1", "p2", "p3", "p4", "p6")


def test_the_keys_of_the_file_that_the_detector_does_not_read_are_ignored(tmp_path: Path) -> None:
    # The data owner may add fields in a minor version (coordinates, zones, influence cells...).
    path = write_toy(tmp_path, future_field={"anything": [1, 2, 3]})

    assert PipeNetwork.from_file(path).pipes == ("p1", "p2", "p3", "p4", "p6")


def test_a_pipe_may_not_start_and_end_at_the_same_node(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="p1"):
        PipeNetwork.from_file(write_toy(tmp_path, {"p1": ["n1", "n1", 10.0]}))


@pytest.mark.parametrize("length", [0.0, -3.0, None])
def test_a_pipe_needs_a_positive_length(tmp_path: Path, length: float | None) -> None:
    with pytest.raises(ValueError, match="p1"):
        PipeNetwork.from_file(write_toy(tmp_path, {"p1": ["n1", "n2", length]}))


def test_a_pipe_that_is_not_open_is_refused(tmp_path: Path) -> None:
    document = network_document(PIPES)
    document["links"][0]["status"] = "Closed"
    path = tmp_path / "network.toy.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="p1"):
        PipeNetwork.from_file(path)


def test_a_link_attached_to_an_undeclared_node_is_refused(tmp_path: Path) -> None:
    document = network_document(PIPES)
    document["nodes"] = [n for n in document["nodes"] if n["id"] != "n5"]
    path = tmp_path / "network.toy.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="n5"):
        PipeNetwork.from_file(path)


def test_a_repeated_link_id_is_refused(tmp_path: Path) -> None:
    document = network_document(PIPES)
    document["links"].append(dict(document["links"][0]))
    path = tmp_path / "network.toy.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="p1"):
        PipeNetwork.from_file(path)


def test_a_network_without_pipes_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no pipe"):
        PipeNetwork.from_file(write_toy(tmp_path, {}))


@pytest.mark.parametrize("version", ["1.1", "2.0", None])
def test_only_schema_version_1_0_is_read(tmp_path: Path, version: str | None) -> None:
    document = network_document(PIPES)
    if version is None:
        del document["schema_version"]
    else:
        document["schema_version"] = version
    path = tmp_path / "network.toy.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        PipeNetwork.from_file(path)
