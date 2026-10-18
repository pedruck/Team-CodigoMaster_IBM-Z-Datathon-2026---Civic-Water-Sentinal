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

## Addendum (2026-10-18, BE-024) — interactive map as the first viewport

**Status of this addendum:** Proposed, with the dashboard owner's approval of the plan.

**What changes.** The decision above placed the map *after* the issued-alert feed. The first
viewport is now a three-pane workspace: the alert queue (about 220 px), the interactive
network map (the widest pane) and the selected alert's key evidence (about 260 px). Selecting
an alert in the queue updates the map, which flies from the whole network to the alert's
region and candidate pipe. The full record, history and system status follow below. On narrow
screens the panes stack as map, queue, evidence. The sidebar starts collapsed so the map has
at least about 700 × 480 px at a 1280 × 720 recording size.

**Why.** The project is judged from a three-minute video, and the operator's first question is
*where to look*. A map after the list made location a scroll away and made the pitch open on
a table. With the queue beside the map, the list is still one glance away; nothing that was
on the page is removed.

**How the map is rendered now.** `map_svg` is unchanged and still serves the SVG download,
slides and tests. The interactive map is a self-contained HTML document
(`dashboard.map_embed.map_document`) loaded with `st.iframe`: local CSS, script and IBM Plex
fonts are inlined, so it works offline and needs no CDN or new dependency. Data and drawing
are separate: the pure function `dashboard.network_map.map_payload(net, alert, alerts)`
builds the JSON (links, sensors, landmarks, regions with their inks, the alerts in time order
and the selection), which travels in a non-executing `<script type="application/json">`
with `<`, `>`, `&`, U+2028 and U+2029 escaped. The script inserts every value as text. The
frame cannot talk back to Streamlit: its replay walks the alerts inside the map and shows a
card for the alert that is lit, while the queue and the evidence pane keep the selection.

**What stays true.** Unknown regions, pipes and sensors are listed as warnings and never
placed. The candidate pipe is always labelled "Suspected leak (best guess)". SIMULATED,
RECORDED REPLAY and the simulated-data caption stay in the first viewport. Regions mode uses
nine inks that avoid the alert coral and the alert-region green and keep at least 4.3:1
against the map ground. `prefers-reduced-motion` removes the pulses and camera flights.
Keyboard: arrows pan, + and − zoom, 0 fits, F focuses the alert, Escape closes details; the
deviating sensors and the candidate pipe are focusable. The other 30 sensors and the
905 pipes are reachable by pointer only (hover and click); revisit if operator testing needs
keyboard access to every element.

**Jupyter.** `NetworkMapView` shows the same document in an `<iframe srcdoc>`, with a
`Static SVG` fallback for front ends that do not run scripts inside that frame. It has not
been run in the VM's JupyterLab yet (NOT_RUN at the time of writing).

**Demo data.** `dashboard/playback/fixture-synthetic-ltown-demo` holds six hand-authored
alerts on official L-Town IDs with invented values, `is_mock: true` and a run note saying it
is synthetic and not a detector output. Any recorded run in the standard playback format
(`run.json`, `status.json`, `alerts.jsonl`) replaces it without code changes.
