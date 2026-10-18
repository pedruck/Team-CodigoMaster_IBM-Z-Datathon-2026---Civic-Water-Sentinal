"""Pipe network of the detector (BE-012g / BE-012i, ADR-011).

The graph is read from the data owner's ``l-town.json`` (schema 1.0), which lives with the other
data in ``$SENTINEL_DATA_DIR/network/v1/`` and is never copied into git. Only what the detector
needs is read: the nodes and the links (ID, kind, two nodes and, for a pipe, its length in
metres); the rest of the file (coordinates, zones, influence cells) is ignored. Pumps and valves
are not pipes: they are listed in ``excluded_links`` and separate the network into *zones*
(groups of nodes joined by pipes).

``PipeNetwork`` answers three questions: how far apart two nodes or two pipes are along the pipes,
which zone a node belongs to and which pipe is the candidate for the leak behind an alert
(``candidate_pipe``). Distances are float64 sums taken in a fixed order, so the result does not
depend on the order of evaluation (the x86 / s390x parity of BE-015 is not measured for it yet).
"""

import hashlib
import heapq
import math
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"


class NetworkSource(BaseModel):
    """The network model the file was built from: its name and the sha256 of its bytes."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    file: str
    sha256: str


class _Node(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str


class _Link(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    type: Literal["pipe", "pump", "valve"]
    start: str = Field(alias="from")
    end: str = Field(alias="to")
    length: float | None = None  # metres; pipes only
    status: str | None = None  # pipes only
    valve_type: str | None = None  # valves only, e.g. "PRV"

    @model_validator(mode="after")
    def _a_pipe_has_a_length_and_is_open(self) -> "_Link":
        if self.type != "pipe":
            return self
        if self.length is None or not (math.isfinite(self.length) and self.length > 0.0):
            raise ValueError(f"pipe {self.id!r} needs a positive length, got {self.length}")
        if self.start == self.end:
            raise ValueError(f"pipe {self.id!r} starts and ends at node {self.start!r}")
        if (self.status or "").upper() != "OPEN":
            raise ValueError(f"pipe {self.id!r} has status {self.status!r}; only Open is supported")
        return self


class NetworkDocument(BaseModel):
    """The part of ``l-town.json`` that the detector reads."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: Literal["1.0"]
    network: str
    source: NetworkSource
    nodes: tuple[_Node, ...]
    links: tuple[_Link, ...]

    @model_validator(mode="after")
    def _links_are_unique_attached_to_declared_nodes_and_include_a_pipe(
        self,
    ) -> "NetworkDocument":
        declared = {node.id for node in self.nodes}
        seen: set[str] = set()
        for link in self.links:
            if link.id in seen:
                raise ValueError(f"link {link.id!r} appears twice")
            seen.add(link.id)
            for end in (link.start, link.end):
                if end not in declared:
                    raise ValueError(
                        f"link {link.id!r} is attached to node {end!r}, which is not declared"
                    )
        if not any(link.type == "pipe" for link in self.links):
            raise ValueError("the network has no pipe")
        return self


class PipeNetwork:
    """The pipe graph and what the detector asks of it. Immutable except for a cache of
    shortest-path distances (``_reach``), which never changes an answer."""

    def __init__(self, document: NetworkDocument, *, network_id: str | None = None) -> None:
        self._doc = document
        pipes = [link for link in document.links if link.type == "pipe"]
        self._pipes = tuple(link.id for link in pipes)
        self._ends = {link.id: (link.start, link.end) for link in pipes}
        self._length = {link.id: link.length or 0.0 for link in pipes}
        adjacency: dict[str, list[tuple[str, float]]] = {}
        for link in pipes:
            adjacency.setdefault(link.start, []).append((link.end, self._length[link.id]))
            adjacency.setdefault(link.end, []).append((link.start, self._length[link.id]))
        self._adjacency = adjacency
        self._nodes = frozenset(adjacency)
        self._excluded = {
            link.id: (
                (link.valve_type or "VALVE").upper() if link.type == "valve" else "PUMP",
                link.start,
                link.end,
            )
            for link in document.links
            if link.type != "pipe"
        }
        self._zone = self._zones()
        self._network_id = (
            network_id
            or hashlib.sha256(
                (document.model_dump_json(indent=2) + "\n").encode("utf-8")
            ).hexdigest()
        )
        self._reach: dict[str, dict[str, float]] = {}

    @classmethod
    def from_file(cls, path: Path) -> "PipeNetwork":
        raw = path.read_bytes()
        return cls(
            NetworkDocument.model_validate_json(raw), network_id=hashlib.sha256(raw).hexdigest()
        )

    @property
    def network_id(self) -> str:
        """sha256 of the file the network was loaded from; for one built in memory, of its
        canonical JSON (the same rule as the calibration and regions ids)."""
        return self._network_id

    @property
    def source(self) -> NetworkSource:
        return self._doc.source

    @property
    def pipes(self) -> tuple[str, ...]:
        """Pipe IDs in file order (the tie-break order of ``candidate_pipe``)."""
        return self._pipes

    @property
    def nodes(self) -> frozenset[str]:
        """The nodes some pipe touches."""
        return self._nodes

    @property
    def excluded_links(self) -> Mapping[str, tuple[str, str, str]]:
        """Pumps and valves, which are not pipes of the graph: ID -> (type, node, node)."""
        return MappingProxyType(self._excluded)

    def ends(self, pipe: str) -> tuple[str, str]:
        return self._ends[pipe]

    def zone_of(self, node: str) -> int:
        """Index of the zone of ``node``: zones are numbered in order of first appearance in the
        file. ``KeyError`` for a node that no pipe touches."""
        return self._zone[node]

    def zone_of_pipe(self, pipe: str) -> int:
        """Index of the zone of ``pipe`` (both its ends are in it)."""
        return self._zone[self._ends[pipe][0]]

    def distance(self, a: str, b: str) -> float:
        """Shortest distance along the pipes between two nodes, in metres; ``math.inf`` between
        different zones."""
        return self._distances_from(a).get(b, math.inf)

    def pipe_distance(self, p: str, q: str) -> float:
        """Distance along the pipes between the midpoints of two pipes, in metres; ``0`` for the
        same pipe and ``math.inf`` between zones. A working definition of this module, not the
        one of the BattLeDIM score (ADR-010 §7: check it against the official code)."""
        if p == q:
            return 0.0
        a, b = self._ends[p]
        leaving = min(self._distance_to_midpoint(a, q), self._distance_to_midpoint(b, q))
        return leaving + self._length[p] / 2.0

    def candidate_pipe(self, weights: Mapping[str, float]) -> str:
        """The pipe whose midpoint minimises ``sum(w * d**2)``, the evidence-weighted sum of
        squared distances along the pipes to the nodes with evidence (ADR-011 §3).

        ``weights`` maps nodes to a positive, finite evidence (the CUSUM statistic ``S`` of the
        deviating sensors). Only the zone with the largest total evidence is used (a leak is in
        one zone; the other nodes could only add infinite distances); a tie between zones goes to
        the zone that appears first in the file, and a tie between pipes to the pipe that appears
        first. ``ValueError`` for no evidence, an unknown node or a weight that is not positive
        and finite."""
        if not weights:
            raise ValueError("no evidence: weights is empty")
        for node, weight in weights.items():
            if node not in self._nodes:
                raise ValueError(f"node {node!r} is not in the network")
            if not (math.isfinite(weight) and weight > 0.0):
                raise ValueError(f"evidence of {node!r} must be positive and finite, got {weight}")
        total: dict[int, float] = {}
        for node, weight in weights.items():
            total[self._zone[node]] = total.get(self._zone[node], 0.0) + weight
        zone = min(total, key=lambda z: (-total[z], z))
        evidence = sorted((n, w) for n, w in weights.items() if self._zone[n] == zone)
        scores = {
            pipe: self._score(pipe, evidence)
            for pipe in self._pipes
            if self.zone_of_pipe(pipe) == zone
        }
        return min(scores, key=scores.__getitem__)  # the first of equal scores, in file order

    # -- internals ---------------------------------------------------------------------------

    def _zones(self) -> dict[str, int]:
        zone: dict[str, int] = {}
        for pipe in self._pipes:
            for start in self._ends[pipe]:
                if start in zone:
                    continue
                index = max(zone.values(), default=-1) + 1
                stack = [start]
                zone[start] = index
                while stack:
                    node = stack.pop()
                    for other, _ in self._adjacency[node]:
                        if other not in zone:
                            zone[other] = index
                            stack.append(other)
        return zone

    def _distances_from(self, source: str) -> dict[str, float]:
        cached = self._reach.get(source)
        if cached is not None:
            return cached
        if source not in self._nodes:
            raise KeyError(source)
        dist = {source: 0.0}
        queue = [(0.0, source)]
        while queue:
            d, node = heapq.heappop(queue)
            if d > dist[node]:
                continue
            for other, length in self._adjacency[node]:
                candidate = d + length
                if candidate < dist.get(other, math.inf):
                    dist[other] = candidate
                    heapq.heappush(queue, (candidate, other))
        self._reach[source] = dist
        return dist

    def _score(self, pipe: str, evidence: list[tuple[str, float]]) -> float:
        """``sum(w * d**2)`` over the evidence, added in the order given."""
        score = 0.0
        for node, weight in evidence:
            d = self._distance_to_midpoint(node, pipe)
            score += weight * d * d
        return score

    def _distance_to_midpoint(self, node: str, pipe: str) -> float:
        """From ``node`` to the midpoint of ``pipe``, through the nearer end of the pipe."""
        a, b = self._ends[pipe]
        reach = self._distances_from(node)
        return min(reach.get(a, math.inf), reach.get(b, math.inf)) + self._length[pipe] / 2.0
