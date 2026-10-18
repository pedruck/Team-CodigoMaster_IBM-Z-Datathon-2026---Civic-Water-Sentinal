# ADR-011 — Pipe network, L-Town regions and the candidate pipe of an alert

- **Status:** Proposed — author: detection owner. Needs confirmation by the backend owner
  (`Alert` contract 1.1, `candidate_pipe`), the evaluation owner (ADR-010 §6 and §7) and the
  data owner (the graph, DEP-DET-06): Q-DET-08 in `docs/questions-for-team.md` asks them.
  Settles the content of the mapping that ADR-007 §8 left to BE-012g, and answers Q-DET-05 and
  Q-DET-07.
- **Date:** 2026-10-17
- **Implementation:** `src/sentinel/detection/network.py` (`PipeNetwork`),
  `src/sentinel/detection/offline/topology.py` (builder, never imported by the server),
  `config/regions.battledim.json`; `CusumDetector(network=...)` and `SENTINEL_DETECTOR_NETWORK`
  (BE-012g, BE-012i). The network itself is the data owner's `l-town.json`, outside git.

## Context
ADR-007 §8 fixed the *format* of the sensor → region file and asked for its *content* to be
"built from the network topology by a documented rule fixed before any evaluation". Nothing in
the repository described the topology (`docs/data.md` §11: the network file was not in `raw/`).
The official BattLeDIM score also needs a candidate pipe per alert and distances along the pipes
(Q-DET-07, DEP-DET-06).

The network model is `L-TOWN.inp` (EPANET, 412 200 bytes, sha256 `a7551b86…622b436`, title
"L-TOWN v1.2", CC BY 4.0) from the BattLeDIM record on Zenodo
(<https://zenodo.org/records/4017659>). The data owner turned it into
`$SENTINEL_DATA_DIR/network/v1/l-town.json` (schema 1.0, sha256 `ff772dc1…bf49b59`, 299 kB), which
records the hash of the model it came from. The model has 905
pipes (43 163 m in total), 785 nodes touched by a pipe, two reservoirs (`R1`, `R2`), one tank
(`T1`), one pump (`PUMP_1`, `n54` → `T1`) and three pressure-reducing valves (`PRV-1`
`n303`→`n300`, `PRV-2` `n336`→`n111`, `PRV-3` `n229`→`n226`). All pipes are `Open`. The IDs of
the pressure sensors (`n1`, …) are nodes of this file and the IDs of the labelled leaks (`p257`,
…) are pipes of it.

## Problem
Give the detector (a) a sensor → region content that does not depend on any evaluation, and (b) a
candidate pipe for each alert, without simulating the hydraulics and without changing what the
detector decides.

## Decision

**1. The network file.** The detector reads the data owner's `l-town.json` (schema 1.0), kept with
the other data in `$SENTINEL_DATA_DIR/network/v1/` and never copied into git (AGENTS §2). Of that
file it reads only `schema_version` (must be `"1.0"`), `source` (the model it came from), the
`nodes` (IDs) and the `links` (ID, kind `pipe` / `pump` / `valve`, two nodes and, for a pipe, its
length in metres and its `Open` status); coordinates, zones and influence cells are ignored, so
the data owner may add to the file. The server refuses (`ConfigError`) a file that has another
schema version, a pipe without a positive length or not `Open`, a repeated link ID, a link at an
undeclared node or no pipe. Pumps and valves are **not** pipes of the graph: they are listed in
`PipeNetwork.excluded_links`. `PipeNetwork.network_id` is the sha256 of the file, as for the
calibration and the regions.

*Zones.* A zone is a group of nodes joined by pipes. Because the pump and the PRVs are not pipes,
the network splits into five zones; three have pressure sensors: the main zone (29 sensors, fed
through `PRV-1` and `PRV-2`), the zone under the tank `T1` (`n1`, `n4`, `n31`, fed by the pump)
and the zone behind `PRV-3` (`n215`). The other two are the reservoirs with their first pipe.

**2. The region rule** (`offline/topology.py:derive_regions`, run by
`python -m sentinel.detection.offline.topology`; `--check` verifies the committed file against a
network file and writes nothing). Computed from the pipes alone:
no sensor reading, residual, calibration or leak label is read.

1. A sensor's zone is that of its node. Sensors of different zones never share a region.
2. Zones are numbered `Z1, Z2, …` by decreasing number of sensors (a tie goes to the zone of the
   sensor that comes first in the registry).
3. Inside a zone, sensors are merged by **complete linkage** on the shortest distance along the
   pipes: the closest pair of groups merges first, as long as the largest distance between any two
   of their sensors stays within **D = 1000 m**. A tie goes to the pair whose first sensors come
   first in the registry.
4. Regions are named `Z<zone>-R<n>`, `n` counting the groups of the zone by the position of their
   first sensor; sensors keep the registry order inside a region; the file lists the regions in
   that order (the tie-break of ADR-007 §8).

D is a round number, about three times the median distance from a pressure sensor to its nearest
neighbour in the same zone (332 m; minimum 199 m, maximum 798 m). It was fixed from the topology
before any evaluation and not tuned on labels or residuals. For the record, the number of regions
and their sizes for other values (all computed from the topology only):

| D (m) | regions | sizes |
|---|---|---|
| 300 | 29 | 1 or 2 sensors each |
| 500 | 18 | 1 to 4 |
| 800 | 13 | 1 to 4 |
| **1000** | **9** | **3, 6, 7, 4, 3, 4, 2, 3, 1** |
| 1500 | 5 | 7, 13, 9, 3, 1 |
| 3000 | 4 | 20, 9, 3, 1 |
| unbounded | 3 (the zones) | 29, 3, 1 |

Result: `config/regions.battledim.json`, sha256 `ca365b70…fe57d`, nine regions: `Z1-R1` … `Z1-R7`
(main zone), `Z2-R1` (`n1`, `n4`, `n31`) and `Z3-R1` (`n215`). The builder never overwrites a
different existing file: another D or another network is a new file name and a new entry here.

**3. The candidate pipe** (`PipeNetwork.candidate_pipe`). When an alert opens, the detector gives
the network the CUSUM statistic `S` of each deviating sensor as its evidence `w` (the same set as
`deviating_sensors`, ADR-007 §8). The candidate is the pipe whose **midpoint** minimises
`Σ w · d²`, with `d` the distance along the pipes from the sensor's node to the midpoint (through
the nearer end of the pipe: `min(d(node, a), d(node, b)) + length/2`). Only the zone with the
largest total evidence counts (a tie: the zone that comes first in the file); a tie between pipes
goes to the pipe that comes first in the file. This is the evidence-weighted barycentre on the
graph. The square makes two equally displaced sensors name a pipe between them (a plain sum of
distances is the same for every pipe on the path); the weights pull it towards the sensors that
accumulated more. With one deviating sensor the candidate is the shortest pipe that touches it:
the honest answer of "somewhere around this node". It is a candidate for a human to inspect, not
a hydraulic localisation.

**4. Contract.** `Alert` v1.1 gains `candidate_pipe: str | null` (optional, default `null`;
`AlertCandidate` too), after `region`. `schema_version` is `"1.1"` for alerts created now and
`"1.0"` payloads are still accepted (contract §6: additive field, minor bump). Without a network
the field is `null`, as with the mock detector. `diagnostics.network_id` (sha256 of the file)
appears only with a network. `SENTINEL_DETECTOR_NETWORK` (optional, needs
`SENTINEL_DETECTOR=cusum`) points to the file (on the VM
`$SENTINEL_DATA_DIR/network/v1/l-town.json`); the server refuses to start (`ConfigError`) if the
file is missing or invalid or if a pressure sensor of the registry is not a node of the network.
Regions and network are independent: either, both or none can be given.

**5. Distances.** `PipeNetwork.distance(a, b)` (nodes) and `pipe_distance(p, q)` (pipes) are
distances along the pipes, `inf` between zones; `pipe_distance` measures between the midpoints.
They exist for the evaluation (DEP-DET-06). They are **not** claimed to be the distance of the
BattLeDIM score: that one is taken verbatim from the official rules and code by the evaluation
owner (ADR-010 §7), in particular how pumps and valves count in the graph. Here a pump or a valve
is not a link, so two zones are infinitely far apart.

## Alternatives considered
- **A copy of the pipes derived from the `.inp` and committed in `config/`:** it agrees with the
  data owner's file on every pipe, node, length and zone (checked), so it adds nothing, it could
  drift from the file the rest of the team uses, and it would put derived BattLeDIM data in git.
- **The three hydraulic zones as regions:** one of them holds 29 of the 33 sensors, so `region`
  would say almost nothing. Kept as level 1 of the rule.
- **A fixed number of regions (k-means or k-medoids):** needs a `k` with no a-priori value;
  a distance bound has a physical reading (how far apart two sensors that see the same leak can
  be) and is stable when a sensor is added.
- **Regions from the labelled leaks (which sensors react to which pipe):** uses the labels and
  makes the 2018 result optimistic and the 2019 one dependent on a fit.
- **Hydraulic simulation of one leak per pipe (signature matching):** the best-known approach in
  the competition, but it needs an EPANET engine (not available on s390x, not in the repository)
  and a model of leak size; left as a future step if the topological candidate proves too coarse.
- **Sum of distances (1-median) instead of squared distances:** ties along the whole path
  between two sensors.
- **Weights from every sensor's `S`:** sub-threshold `S` has a positive mean without a leak
  (ADR-007 §8); only the evidence above `h` is used, like the region ranking.
- **Pumps and valves as links of length 0:** a modelling choice that joins zones the hydraulics
  keeps apart; it can be added later if the evaluation's distance requires it.

## Evidence (development, 2018 only; not a result)
The detector of BE-012f with its development calibration (`provenance = dev-baseline-2018`,
`is_mock = true`; h = 8, N = 2, k = 0.5; nothing about it is pre-registered) was replayed on the
out-of-fold 2018 residuals of the hour-of-week baseline with the committed regions and the data
owner's network
(a scratch script, not committed; no 2019 file was opened). The inputs are the files of that run,
which live only under `~/shared/civicwater/data/p4_detect/dev-baseline-2018/` and are in no branch
(calibration file sha256 `7057b5c0…bb849`); that is why the plan and the status still describe
BE-012f as without a result. The replay reproduces the 1 487 openings of the run's `report.md`
(row h = 8, N = 2, which counts 710 of them as false alarms under its provisional matching
window) and each opening carries a candidate pipe. It takes 13–16 s for 105 120 windows.
Distances are `pipe_distance` (midpoint to midpoint); a labelled leak is active at an opening when
the opening's window falls in the leak's `[start, end]`.

- **All openings.** 1 478 of the 1 487 have at least one leak active, and 1 461 of those have a
  finite distance (17 are in a zone with no active leak). Over these 1 461 the candidate is a
  median 621 m from the nearest active leak (quartiles 365 and 861 m), against 677 m for a pipe
  drawn at random in the same zone (median of 40 draws per opening); 19.1 % are within 300 m
  against 14.2 % for the random pipe. That is hardly better than chance, as expected for openings
  that are mostly repeats and false alarms.
- **First opening within 24 h of the 6 abrupt leaks** (`p673`, `p538`, `p866`, `p183`, `p158`,
  `p369`; from 15 min to 1 h 45 min after the start): the candidate is 363, 2 892, 0, 346, 220 and
  323 m from the leaking pipe, against 189, 1 017, 1 977, 1 892, 1 140 and 1 188 m for the median
  pipe of the same zone. Five of six are within about 360 m of the leaking pipe. Four of six are
  closer than the median pipe of their zone; `p673` (363 m against 189 m) is not, because its zone
  is small, and `p538` is 2 892 m away, further than the median pipe (1 017 m).

Six events with an uncalibrated detector support only "the rule is not absurd on abrupt leaks";
they do not measure localisation. The 2019 run is the only valid measure.

## Pros
No hydraulic model, no dependency, no label. One deterministic function of the alert's own
evidence. Regions and candidate come from one file the data owner can replace with their graph.
The format of ADR-007 §8 is unchanged.

## Cons
A topological guess: it ignores flows, demands, leak size and the direction of the flow, so a
leak between two sensors that react unequally can be named on the wrong side. One deviating
sensor gives only "a pipe next to it". D is a choice (see the table). The candidate is not
tested on incipient leaks, which the development run does not detect (ADR-008).

## Consequences
- Contract §3 and §4, the alert example, the plan, the status and the dependencies are updated;
  `Alert` is 1.1. The audit payload of `alert.raised` gains `candidate_pipe` (additive).
- The determinism digest of `tests/detection/test_determinism.py` changed because every alert now
  serialises `candidate_pipe: null`; without that key the old digest is reproduced exactly
  (checked), so the numbers of the detector did not change. The s390x run (BE-015) must use the
  new literal.
- **For the evaluation (ADR-010, ADR-008 §G):** the pipe → region table for the labels needs no
  label to be built. `l-town.json` already gives, for each link, its `nearest_pressure_sensor`
  (the data owner's), and the regions file maps that sensor to a region. For comparison, the
  nearest sensor to each pipe's midpoint along the pipes (`PipeNetwork`) is the same for 903 of the
  905 pipes; the evaluation owner decides which definition to register (ADR-008 §G keeps the
  field as theirs). The dashboard (P6) does not show the candidate pipe yet.

## Risks
- A different network file (the Zenodo record also has `L-TOWN_Real.inp`) would give a different
  graph, and a regions file built from another network would not fit it. `source.sha256` records
  the model, a test pins the sha256 of `l-town.json` and of the regions file, and
  `topology --check` says whether the regions file is what the rule gives for the file at hand.
- On the LinuxONE VM the server needs the file at `network/v1/l-town.json` of its data directory;
  without it, `SENTINEL_DETECTOR_NETWORK` makes the start fail (it is optional: unset, the alert
  has no `candidate_pipe`).
- If the evaluation's distance counts pumps and valves as links, `pipe_distance` and the
  official one disagree across zones; the zones are far from each other either way.
- A client that rejects unknown fields and still validates against 1.0 will refuse 1.1 alerts.

## Dependencies
DEP-DET-04 (resolved by this ADR), DEP-DET-06 (candidate pipe: done; official distance: evaluation
owner), Q-DET-05, Q-DET-07, ADR-007 §8, ADR-008 §G, ADR-010 §6–7.

## Revisit when
The data owner supplies a graph of their own, the 2019 run shows the candidate no better than
the median pipe, hydraulic simulation becomes feasible on the target platform, or the official
BattLeDIM distance is read from the competition code.
