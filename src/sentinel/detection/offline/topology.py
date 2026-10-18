"""Sensor -> region mapping from the pipe network (BE-012g, ADR-011 §1 and §2).

Usage::

    python -m sentinel.detection.offline.topology           # writes config/regions.battledim.json
    python -m sentinel.detection.offline.topology --check   # verifies that file, writes nothing

The pipes are read from the data owner's ``l-town.json`` (``$SENTINEL_DATA_DIR/network/v1/``,
``PipeNetwork``) and the regions are derived from the pipes alone by a fixed rule
(``derive_regions``). No sensor reading, leak label, residual or calibration is read, so the
mapping cannot depend on what any evaluation shows. Development tool: never imported by the
server, which reads only the files this checks or writes.
"""

import argparse
import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from sentinel.detection.network import PipeNetwork
from sentinel.sensors import SensorRegistry

# The distance that bounds a region (ADR-011 §2): sensors join while every pair stays within it.
# About three times the median distance from a pressure sensor to its nearest neighbour (332 m);
# fixed from the topology before any evaluation, never tuned on labels or residuals.
REGION_DIAMETER_M = 1000.0
NETWORK_FILE = Path("network") / "v1" / "l-town.json"


def derive_regions(
    network: PipeNetwork, sensors: Sequence[str], *, diameter_m: float = REGION_DIAMETER_M
) -> dict[str, tuple[str, ...]]:
    """Group the sensors into regions by the rule of ADR-011 §2.

    1. A sensor's zone is the group of nodes its node is joined to by pipes; pumps and valves
       separate zones. Sensors of different zones never share a region.
    2. Zones are numbered 1, 2, ... by decreasing number of sensors (a tie goes to the zone of
       the sensor that comes first in ``sensors``).
    3. Inside a zone the sensors are merged by complete linkage on the distance along the pipes:
       the closest pair of groups merges first, as long as the largest distance between any two
       of their sensors stays within ``diameter_m`` (a tie goes to the pair whose first sensors
       come first in ``sensors``).
    4. Regions are named ``Z<zone>-R<n>``, ``n`` counting the groups of the zone by the position
       of their first sensor. Sensors keep the order of ``sensors`` inside a region.
    """
    if not (math.isfinite(diameter_m) and diameter_m > 0.0):
        raise ValueError(f"diameter_m must be positive and finite, got {diameter_m}")
    for sensor in sensors:
        if sensor not in network.nodes:
            raise ValueError(f"sensor {sensor!r} is not a node touched by a pipe of the network")
    zones: dict[int, list[int]] = {}
    for position, sensor in enumerate(sensors):
        zones.setdefault(network.zone_of(sensor), []).append(position)
    ordered = sorted(zones.values(), key=lambda members: (-len(members), members[0]))
    regions: dict[str, tuple[str, ...]] = {}
    for zone_number, members in enumerate(ordered, start=1):
        groups = _complete_linkage(network, sensors, members, diameter_m)
        for group_number, group in enumerate(groups, start=1):
            regions[f"Z{zone_number}-R{group_number}"] = tuple(sensors[p] for p in group)
    return regions


def _complete_linkage(
    network: PipeNetwork, sensors: Sequence[str], members: list[int], diameter_m: float
) -> list[list[int]]:
    """Groups of positions in ``sensors``, each sorted, the groups sorted by first position."""
    groups = [[p] for p in members]
    while True:
        best: tuple[float, int, int] | None = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                linkage = max(
                    network.distance(sensors[a], sensors[b]) for a in groups[i] for b in groups[j]
                )
                key = (linkage, groups[i][0], groups[j][0])
                if linkage <= diameter_m and (best is None or key < best):
                    best = key
        if best is None:
            return groups
        first, second = best[1], best[2]
        i = next(n for n, g in enumerate(groups) if g[0] == first)
        j = next(n for n, g in enumerate(groups) if g[0] == second)
        groups[i] = sorted(groups[i] + groups[j])
        del groups[j]


def render_regions(regions: Mapping[str, Sequence[str]]) -> str:
    """The regions file of ADR-007 §8, one region per line, in tie-break order."""
    body = ",\n".join(
        f"    {json.dumps(region)}: {json.dumps(list(sensors))}"
        for region, sensors in regions.items()
    )
    return f'{{\n  "regions": {{\n{body}\n  }}\n}}\n'


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    data_dir = os.environ.get("SENTINEL_DATA_DIR")
    default_network = Path(data_dir).expanduser() / NETWORK_FILE if data_dir else None
    ap.add_argument(
        "--network",
        type=Path,
        default=default_network,
        help="the pipe network, l-town.json (default: $SENTINEL_DATA_DIR/network/v1/l-town.json)",
    )
    ap.add_argument("--registry", type=Path, default=Path("config/sensors.battledim.json"))
    ap.add_argument("--out-dir", type=Path, default=Path("config"))
    ap.add_argument("--name", default="battledim", help="the file is regions.<name>.json")
    ap.add_argument("--diameter-m", type=float, default=REGION_DIAMETER_M)
    ap.add_argument(
        "--check", action="store_true", help="verify the existing file instead of writing it"
    )
    args = ap.parse_args(argv)
    if args.network is None:
        raise SystemExit("pass --network or set SENTINEL_DATA_DIR (network/v1/l-town.json)")

    network = PipeNetwork.from_file(args.network.expanduser())
    pressure = SensorRegistry.load(args.registry).pressure
    regions = derive_regions(network, pressure, diameter_m=args.diameter_m)
    path = args.out_dir / f"regions.{args.name}.json"
    content = render_regions(regions).encode("utf-8")

    if args.check:
        if not path.exists():
            raise SystemExit(f"{path} does not exist")
        if path.read_bytes() != content:
            raise SystemExit(f"{path} differs from what the rule gives for {args.network}")
        print(
            f"{path}: matches the rule on {args.network.name} "
            f"(sha256 {network.network_id[:12]}…, built from {network.source.file})"
        )
        return 0
    if path.exists() and path.read_bytes() != content:
        # The mapping is fixed before any evaluation: a change is a new file, never an overwrite.
        raise SystemExit(f"{path} exists and differs from what this rule produces; not overwriting")
    path.write_bytes(content)
    print(f"{path}: {len(regions)} regions for {len(pressure)} pressure sensors")
    for region, members in regions.items():
        print(f"  {region}: {', '.join(members)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
