"""Toy pipe-network files in the format of the data owner's ``l-town.json`` (schema 1.0), shared
by the detection tests: invented pipes and nodes; none of it describes the L-Town network."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# Extra keys the real file carries and the detector ignores; the toy files carry them too.
EXTRAS: dict[str, Any] = {
    "units": {"length": "m"},
    "bbox": [0, 0, 1, 1],
    "zones": {},
    "influence": {},
}


def network_document(
    pipes: Mapping[str, list[Any]],
    excluded: Sequence[tuple[str, str, str, str]] = (),
    **overrides: Any,
) -> dict[str, Any]:
    """``pipes`` maps a pipe ID to ``[node, node, length_m]``; ``excluded`` lists
    ``(link ID, kind, node, node)`` with kind ``"PUMP"`` or a valve type such as ``"PRV"``."""
    ids = [n for ends in pipes.values() for n in ends[:2]]
    ids += [n for _, _, a, b in excluded for n in (a, b)]
    nodes = [{"id": n, "type": "junction", "x": 0.0, "y": 0.0} for n in dict.fromkeys(ids)]
    links: list[dict[str, Any]] = [
        {"id": p, "type": "pipe", "from": a, "to": b, "length": length, "status": "Open"}
        for p, (a, b, length) in pipes.items()
    ]
    for link_id, kind, a, b in excluded:
        if kind == "PUMP":
            links.append({"id": link_id, "type": "pump", "from": a, "to": b})
        else:
            links.append({"id": link_id, "type": "valve", "from": a, "to": b, "valve_type": kind})
    return {
        "schema_version": "1.0",
        "network": "TOY",
        "source": {"file": "toy.inp", "sha256": "0" * 64},
        **EXTRAS,
        "nodes": nodes,
        "links": links,
        **overrides,
    }


def write_network(
    path: Path,
    pipes: Mapping[str, list[Any]],
    excluded: Sequence[tuple[str, str, str, str]] = (),
    **overrides: Any,
) -> Path:
    path.write_text(json.dumps(network_document(pipes, excluded, **overrides)), encoding="utf-8")
    return path
