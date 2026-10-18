# Operator workspace — BE-021 / BE-022 / BE-023 / BE-025

The primary dashboard remains Streamlit. BE-022 aligns the page with the public preview
of the [CivicWater Figma design](https://www.figma.com/design/vvZqt0ypTmooZpYLn93u9h/CivicWater-Sentinel-%E2%80%94-Painel-operacional?node-id=5-1518),
adds the official L-Town network schematic and keeps the existing source adapters, polling,
schemas and evidence. The Figma URL returned 403 for the full frame in this environment;
the public thumbnail exposed its colour/type system, and exact visual fidelity needs the
owner's review.

Issued alerts are the first operational section. The owner-requested readability pass
keeps the approved visual identity and changes the task order to **alerts → evidence →
history → system status**. The accompanying [UX review](dashboard-ux-review.md) records
the findings, application of UI/UX Pro Max and measured checks.

## Visual direction

A light monitoring workspace with a dark teal sidebar, green for network context and
coral for alerts. IBM Plex Sans/Mono fonts and Phosphor icons are local. The sidebar links
to real sections and the full network map; it collapses using Streamlit's native mobile
navigation. The issued-alert feed remains the first operational section.

References: [Codrops](https://tympanus.net/codrops/hub/), [GSAP](https://gsap.com/),
[Raylight](https://www.raylight.app/templates),
[Hairline](https://hairline.lucasmarkes.com/figures#interfaces), [21st.dev](https://21st.dev/),
[Dot Matrix](https://dotmatrix.zzzzshawn.cloud/), [Shapes Gallery](https://www.shapes.gallery/),
[Rare UI](https://www.rareui.com/components) and [Flaticon](https://www.flaticon.com/).
These inform the composition, rather than adding component dependencies. The local
`minimalist-ui` and `gsap-core` skills informed the layout and motion.

Main alert text and controls use 16 px, supporting text at least 14 px, and native main
buttons at least 44 px height. Recent alerts show five readable summaries with issued time,
region, deviating-sensor count, strongest signed difference and simulated age. Every loaded
record and column remains in **All alert fields**; history keeps its filters and pagination.
**View evidence** selects that record and moves keyboard focus to the detail heading after
an explicit action. Polling does not request that focus. Technical metrics and diagrams
remain in the initially collapsed **System status** section. Notices are grouped without
removing their required content. Controls and labels reflow with enlarged text.

## What the visualisations mean

| Element | Source | Interpretation |
|---|---|---|
| Pressure sensors | `status.sensor_registry.pressure` | Registry count, not a claim that every sensor is healthy |
| Accepted windows | `status.counters.windows_accepted` | Cumulative accepted windows in this run |
| Stored alerts | `status.counters.alerts_stored` | Stored records, not active or confirmed leaks |
| Pipeline p95 | `status.latency.total.p95_ms` | Source-reported pipeline latency |
| Processing profile | Each stage's `p95_ms` | Shared length scale; these are not pressure trends and stage p95s are not added |
| Window buffer | `history.contiguous`, `capacity`, `required` | Contiguous history relative to capacity; warm-up compared to the backend requirement |
| Sensor deviations | `alert.deviating_sensors[].deviation_m` | Signed observed-minus-expected pressure head, metres; zero at the centre |
| Network map | `config/l-town-map.json`, selected `Alert` | All official link segments and 33 pressure sensors; selected region in green, reported deviations and optional candidate pipe in coral. The candidate is a best guess, not a confirmed leak |

The map is shown after selecting an alert and still draws the network when no alert exists.
On narrow screens it scrolls inside its own focusable region, keeping labels at their full
size. The dedicated Streamlit page selects an alert in time order and downloads SVG; the
Jupyter view offers the same map with a selection slider. Source: [BattLeDIM 2020](https://zenodo.org/records/4017659),
CC BY 4.0. The map JSON was generated from the official `L-TOWN.inp` with SHA-256
`a7551b86745f4cc3433c78e60023077fa1386947d4d35372ffd3a0405622b436`; observed
map JSON SHA-256 is `e165e9fdb15521af7673e0c2f3f2e764382ae5547e8e32c25f9a6e721472e9a4`,
which differs from the digest in the task prompt (ADR-012). Counts and all 12 map tests pass.

Summary values are rounded for readability. Expand **Sensor readings · exact values** or
**Full record & audit reference** to inspect the unchanged received values. Missing values
remain missing (`—` / `not provided`); an unavailable API does not become a zero-count run.
The alert selector and all history filters keep their existing behaviour and keys.

The simulated clock still comes from `status.last_timestamp`. The SIMULATED, RECORDED
REPLAY and provisional-registry banners are retained. The recording note and full model
identities are in **Source & models**. An audit hash is a raw reference, and explanation
remains unavailable until its read contract exists.

## Motion and local assets

GSAP core, IBM Plex fonts and the icon subset are committed with their upstream notices in
`dashboard/assets/` (versions and licences: [assets README](../dashboard/assets/README.md)).
No Node build or external asset service is needed to run the dashboard.

The optional static JavaScript enhancement uses `st.html(unsafe_allow_javascript=True)`
only for trusted local source files. Source strings are HTML-escaped and never placed in
JavaScript. SVGs are local data-URI images because Streamlit's HTML-only sanitiser removes
inline SVG elements. The GSAP loader inserts the local library as script text without
`eval` or a remote script URL.

Entrance motion uses only opacity and transforms with short staggered reveals. It is
disabled by `prefers-reduced-motion`; observers and GSAP contexts have cleanup when
reinitialised. Content is visible before enhancement or if it fails. Section navigation
and Streamlit controls work without the motion script.

## Run locally

From the repository root:

```sh
uv sync --locked --group dashboard
just dashboard http://127.0.0.1:8000
# Or preview the committed synthetic recording:
just dashboard-playback dashboard/playback/fixture-cusum
```

On Windows PowerShell, without `just`:

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:SENTINEL_DASHBOARD_PLAYBACK_DIR = 'dashboard/playback/fixture-cusum'
uv run --group dashboard streamlit run dashboard/app.py --server.address 127.0.0.1
```

Open `http://127.0.0.1:8501`. This fixture is an invented synthetic day with a mock
forecaster and a synthetic CUSUM calibration. The recording/simulation banners stay visible.
Its invented region `R-SYN-A` is outside the official L-Town region mapping, so the map
shows the network and labels that region as unavailable. The BE-022 screenshots use a
separate, hand-authored synthetic alert with official topology IDs (`Z1-R3`, `p4`, `n163`,
`n188`) solely to review the map's appearance; it is not a detector output.
For the API, remove `SENTINEL_DASHBOARD_PLAYBACK_DIR` and set `SENTINEL_DASHBOARD_API_URL`
to the existing backend or tunnel.

## Maintenance

`dashboard/presentation.py` contains rendering helpers; `dashboard/assets/theme.css`
adjusts the native theme and named containers. Some selectors use Streamlit's `data-testid`
attributes, so browser checks should accompany a Streamlit upgrade. The dependency lock
and runtime backend are unchanged. The Jupyter alternative keeps its existing appearance.

Pressure/residual history, physical network maps and audit validation still require their
respective data contracts; no substitute data is introduced by this refresh.

## Verification — 2026-10-17

Windows x86-64, Python 3.12.14, Streamlit 1.66.0. Because C had about 12 MB free, the locked
environment and caches were installed at `E:\Codex\civicwater-sentinel` without changing
the project dependencies. `just` was not installed, so its four gate commands were run
directly with that environment's Python (with `PYTHONUTF8=1`, `PYTHONPATH` set to the repo,
`PYTHONDONTWRITEBYTECODE=1`, and `TEMP`/`TMP` redirected to E):

```powershell
$taskPython = 'E:\Codex\civicwater-sentinel\venv\Scripts\python.exe'
& $taskPython -m ruff format --check src tests scripts dashboard
& $taskPython -m ruff check src tests scripts dashboard
& $taskPython -m mypy
& $taskPython -m pytest -q -o cache_dir=E:/Codex/civicwater-sentinel/pytest-cache --basetemp=E:/Codex/civicwater-sentinel/pytest-tmp
```

Results after the readability/UX refinement: **138 files formatted**, lint passed, mypy
**137 files** with no issues, **741 passed, 34 skipped in 42.36s**. The skips require external
data or optional ML dependencies. Thirteen new tests cover source-derived charts,
unknown/empty states, signed/exact values, escaping, refresh/offline metrics, alert-first
ordering, selecting a specific record and retaining records beyond the five summaries.
The existing 116 dashboard tests passed as well.

The first full Windows run exposed two pre-existing guard failures. `.gitattributes` now
keeps the hash-pinned regions file at LF on checkout (the committed JSON is unchanged),
and the evaluation guard normalises path separators before excluding its own package.

Browser commands: `node E:\Codex\civicwater-sentinel\browser\validate-alerts.cjs` and
`node E:\Codex\civicwater-sentinel\browser\audit-accessibility.cjs` (local scratch Playwright
scripts, installed Chrome; `TEMP`/`TMP` on E). The first checked 1536×1080, 390×844, 360×800,
768×1024 and 1440×900; the last used reduced motion. The first alert summary was visible
at every size, with measured 16/14 px alert text and system metrics initially collapsed.
All layouts stayed within the available page width. Evidence navigation, raw audit
disclosure, history filtering/reset and refresh passed. All local assets loaded;
**0 page errors**, **0 external requests**, **0 GSAP tweens** with reduced motion.
Earlier checks of **Source & models** confirmed that full model names/versions wrap.

The second script checked primary-feed contrast (including inherited opacity), keyboard
activation, focus after evidence selection and after polling, ordered headings and 44 px
evidence-button height at 1536 and 390 px. Main text enlarged to 200% at both widths without
horizontal page overflow; wide tables retain their own scrolling. Warning text measured
8.12:1, the sampled feed text at least 5.03:1 and caption text 5.90:1. Chart bars measure
3.85:1 / 4.00:1 against white, with direct value labels. These are focused checks, not a
full WCAG audit, screen-reader rehearsal or a performance score.

Latest captures use the explicitly labelled hand-authored synthetic map preview described
above; these are not detector outputs:

- [Desktop issued alerts](images/dashboard-desktop.png)
- [Selected alert on the network map](images/dashboard-map.png)
- [Alert evidence and history](images/dashboard-evidence.png)
- [Mobile issued alerts](images/dashboard-mobile.png)

## Responsive repair verification — BE-023 (2026-10-18)

The previous CSS applied a 270 px minimum width even when Streamlit collapsed the sidebar.
At 768 px, the hidden sidebar still reserved that width. Width limits now apply only to the
expanded state. Its collapse icon remains visible on the dark surface, navigation links
retain 44 px targets, and accumulated margins no longer push the team footer below a
900 px viewport. Longer content still scrolls vertically.

The native theme still referenced removed Geist fonts; it now names the locally bundled
IBM Plex families, including native widgets and headings. Removing Streamlit's negative
markdown heading margins makes the refresh button and heading share the same centre line.
Action buttons stretch within their column and stack by content width. Status/map labels,
avatars and footer copy accommodate enlarged text. The network map participates in current
section tracking, and explicit section navigation dismisses the sidebar at mobile widths
and focuses the destination heading.

`scripts/check_dashboard_responsive.cjs` is an optional browser regression check, independent
of the Python runtime. It requires Node and Playwright installed outside the repository.
Run it against the single-alert `fixture-cusum` playback or the labelled local map preview.
An existing Chrome installation can be used without downloading another browser:

```powershell
# One-time optional tooling setup; no application dependencies change.
npm.cmd install --prefix E:/Codex/civicwater-sentinel/browser playwright
$env:NODE_PATH = 'E:\Codex\civicwater-sentinel\browser\node_modules'
$env:CHROME_EXECUTABLE = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
node scripts/check_dashboard_responsive.cjs http://127.0.0.1:8501 E:/Codex/civicwater-sentinel/ui-check
```

Observed: **360, 390, 768, 1024, 1280, 1440 and 1920 px**, each 900 px high, passed open/
collapsed navigation, restored page width, map-section state and mobile dismissal,
heading/button alignment (within 2 px on shared rows), 44 px main targets, exact-record
disclosure, history filtering/reset, local font checks and **200% root text** without page
or sidebar horizontal overflow. Wide evidence tables and the map retain internal scrolling.
The checker writes JSON measurements and screenshots to the supplied output directory.

The existing scratch checks `browser/audit-accessibility.cjs` and `browser/validate-map.cjs`
under `E:\Codex\civicwater-sentinel` passed again: primary-feed text contrast at least
**5.27:1**, warning **8.12:1**, keyboard evidence selection/focus through polling, ordered
headings, map/font loading and full map/SVG control. No page errors were observed.
These checks use desktop Chrome with responsive viewports; native mobile browsers and
spoken screen-reader output have not been assessed.

Python gates used the same environment and cache paths above: ruff format **164 unchanged**,
lint passed, strict mypy **163 files**, pytest **894 passed, 39 skipped in 40.23s**.

Additional review captures: [mobile sidebar](images/dashboard-sidebar-mobile.png),
[mobile alert controls](images/dashboard-responsive-mobile.png) and
[sidebar with 200% text](images/dashboard-sidebar-text-200.png).

## Interactive network map — BE-025 (2026-10-18)

The first viewport is now **queue | map | evidence** (ADR-012 addendum, chosen by the owner on
the Impeccable decision page from three sketched structures). The sidebar starts collapsed.

| Pane | Content | Notes |
|---|---|---|
| Recent alerts (≈220 px) | Native radio queue, newest first: region, time, sensor count, largest deviation | Selecting an alert reruns the page; map and evidence follow. Identical summaries add the alert ID. The poll still runs in its fragment and only reports count and errors. |
| Network map (rest of the width) | Live SVG of all 909 links, 33 sensors and three landmarks in an `st.iframe` | Alert/Regions tabs, Focus alert, zoom, Whole network; replay bar; legend; attribution. |
| Evidence (≈260 px) | Region, ID, SIMULATED badge, CUSUM score against its threshold (one scale), window end, onset, candidate pipe "best guess", deviation bars, Explanation slot, link to the full record | Values as received; "explanation unavailable" until Q-AUD-07. |

Below the fold: alert detail (exact tables), all alert fields, history, system status.
Mandatory notices sit above the panes in one line: RECORDED REPLAY, SIMULATED and the
simulated-data caption. On narrow screens the panes stack as map, queue, evidence.

### Map interactions

| Input | Effect |
|---|---|
| Wheel / trackpad pinch, **+** / **−** buttons or keys | Zoom around the pointer or the centre |
| Drag (mouse), two-finger pinch and pan (touch), arrow keys | Pan. On touch, one vertical finger still scrolls the page (`touch-action: pan-y`). |
| Double click | Zoom in twice around the point |
| **Whole network** button or **0** | Fit the network |
| **Focus alert** or **F** | Camera flight to the alert: its candidate pipe and deviating sensors, or its region when no pipe is named. Runs on open after the whole network is shown. |
| Hover | Pipe: ID, type, diameter, region, nearest pressure sensor. Sensor: region and, in the alert, deviation and z. |
| Click / Enter on a focusable element | Details panel inside the map; focus moves to its title, Escape or Close returns it |
| Regions tab | Nine region inks; the legend buttons isolate a region (press again to show all) |
| Replay | A simulated clock walks the alerts in time order; each lights up, the camera flies to it and a card shows region, time, largest deviation and sensor, onset, CUSUM and the candidate pipe (best guess). The card stays on pause, says when the recorded alerts end, and "Back to selected alert" restores the selection. While another alert is lit, a note says the side panels still show the selected one. |

Labels are laid out in priority order (leak callout, deviating sensors, region, landmarks) and
move right/left/above/below to avoid each other and the candidate pipe. Unknown region, pipe
or sensor IDs are listed in a warning and never drawn. `prefers-reduced-motion` removes pulses
and camera flights. Region inks: nine colours at least 4.3:1 against the map ground, none of
them the alert coral or the alert-region green (`REGION_PALETTE`, tested).

### Data and safety

`map_payload(net, alert, alerts)` is pure and returns JSON-ready data (compact link/sensor
arrays, regions with their inks, alerts oldest first with `region_known`, `candidate_known`,
`warnings`, strongest deviation). `map_document` escapes `<`, `>`, `&`, U+2028 and U+2029 in the
JSON; the script only writes text and attributes (a test refuses `innerHTML`, `eval` and network
calls in it). `map_svg` is unchanged and still provides the SVG download.

### Demo data

`dashboard/playback/fixture-synthetic-ltown-demo` has six **hand-authored, synthetic** alerts on
official region, sensor and pipe IDs (values invented, `is_mock: true`, run note says it is not
a detector output). Any recorded run in the standard format works without code changes:

```sh
SENTINEL_DASHBOARD_PLAYBACK_DIR=dashboard/playback/fixture-synthetic-ltown-demo \
  PYTHONPATH=. uv run --group dashboard streamlit run dashboard/app.py
```

### Jupyter (VM)

`NetworkMapView` shows the same document in an `<iframe srcdoc>` (`Map: Interactive`), with
`Map: Static SVG` as the fallback when the front end does not run scripts in that frame.
**NOT_RUN** in the VM's JupyterLab: check with `view = NetworkMapView(source); view.refresh();
view.widget` and switch to `Static SVG` if the iframe stays blank.

### Verification — BE-025

See `docs/dashboard-ux-review.md` (BE-025) for the Impeccable critique, audit and detector
results, and `docs/status.md` for the gates. Captures: [1440 × 900](images/dashboard-map.png),
[1280 × 720](images/dashboard-map-1280.png), [390 × 844](images/dashboard-map-mobile.png),
[replay](images/dashboard-map-replay.png), [Regions](images/dashboard-map-regions.png),
[map page](images/dashboard-map-page.png), [full record below the map](images/dashboard-map-record.png).
All use the synthetic demo playback.
