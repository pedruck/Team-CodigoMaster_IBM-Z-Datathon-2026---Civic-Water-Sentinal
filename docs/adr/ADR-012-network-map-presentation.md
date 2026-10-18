# ADR-012 — Source-derived L-Town map in the operator dashboard

Status: Proposed (2026-10-18)

## Context and problem

ADR-009 keeps the dashboard read-only and contract-bound. ADR-011 adds a region and an
optional candidate pipe to alerts. Operators need to locate those fields on the simulated
L-Town network without loading the private 2018/2019 data or presenting a best guess as a
confirmed leak. The first visible task remains the issued-alert feed (BE-021).

## Decision

- `sentinel.data.export_map` reduces the official BattLeDIM L-Town topology and the committed
  region mapping to `config/l-town-map.json`: link segments, pressure sensor locations and
  region membership, tank/reservoir locations, source hashes and attribution. No SCADA series,
  event labels or model artefacts enter the repository.
- `dashboard.network_map.map_svg` is a pure renderer. The selected alert supplies region,
  sensor deviations and candidate pipe. Missing or unknown IDs are labelled rather than
  guessed. A candidate is explicitly a best guess; mock output stays marked SIMULATED.
- The operator page places the map with selected-alert evidence after the issued-alert feed.
  A separate Streamlit page walks the available alerts in time order and downloads SVG.
  `NetworkMapView` reuses the same renderer in JupyterLab.
- Streamlit embeds the SVG as a local data-URI image because its sanitized `st.html` path
  does not reliably retain inline SVG. A focusable, horizontally scrollable map region keeps
  labels readable on narrow screens and does not overflow the page.
- The interface uses the dark teal, green and coral direction and IBM Plex typography shown
  in the public Figma preview. The full Figma frame could not be retrieved in this environment
  (the design URL returned 403), so exact frame fidelity remains a visual review item.

## Alternatives

Loading the full EPANET file in Streamlit would introduce a private runtime data dependency.
A map tile service would add network access and imply geographic precision absent from the
network coordinates. An HTML canvas would duplicate the vector rendering required for
Jupyter and slides. These were not chosen.

## Consequences and risks

The committed map is reproducible from the official `L-TOWN.inp` and region JSON. It is
about 115 KB and has 909 links, 33 pressure sensors and three landmarks. The source file's
SHA-256 matches the task prompt; the generated map's SHA-256 differs from the prompt's
expected digest even though counts and map tests pass. Keep the observed digest in the
verification record and investigate if another machine produces different bytes. The map
is a network schematic, not a geographic basemap. The SVG image exposes one descriptive
alternative text; fine-grained pipe titles are available in the downloadable SVG, while
exact alert values remain in the dashboard's tables.

## Dependencies and revisit conditions

Depends on ADR-009, ADR-011, `Alert` 1.1 and `config/regions.battledim.json`. Revisit if
the alert contract gains verified location or lifecycle status, if the official topology
changes, or if operator testing shows that the map needs keyboard-addressable individual
network elements.
