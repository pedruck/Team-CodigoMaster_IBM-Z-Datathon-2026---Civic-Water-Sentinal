# Operator workspace — BE-021

The primary dashboard remains Streamlit. This refresh changes presentation and adds an
explicit **Refresh data** action; it does not replace the source adapters, polling logic,
schemas or the Jupyter renderer.

## Visual direction

A light monitoring workspace with a restrained water-green accent, local Geist typography,
Phosphor regular icons and fine structural lines. The sidebar links to actual sections on
the page; it collapses using Streamlit's native mobile navigation.

References: [Codrops](https://tympanus.net/codrops/hub/), [GSAP](https://gsap.com/),
[Raylight](https://www.raylight.app/templates),
[Hairline](https://hairline.lucasmarkes.com/figures#interfaces), [21st.dev](https://21st.dev/),
[Dot Matrix](https://dotmatrix.zzzzshawn.cloud/), [Shapes Gallery](https://www.shapes.gallery/),
[Rare UI](https://www.rareui.com/components) and [Flaticon](https://www.flaticon.com/).
These inform the composition, rather than adding component dependencies. The local
`minimalist-ui` and `gsap-core` skills informed the layout and motion.

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

Summary values are rounded for readability. Expand **Sensor readings · exact values** or
**Full record & audit reference** to inspect the unchanged received values. Missing values
remain missing (`—` / `not provided`); an unavailable API does not become a zero-count run.
The alert selector and all history filters keep their existing behaviour and keys.

The simulated clock still comes from `status.last_timestamp`. The SIMULATED, RECORDED
REPLAY and provisional-registry banners are retained. The recording note and full model
identities are in **Source & models**. An audit hash is a raw reference, and explanation
remains unavailable until its read contract exists.

## Motion and local assets

GSAP core, fonts and the icon subset are committed with their upstream notices in
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

Results: **138 files formatted**, lint passed, mypy **137 files** with no issues,
**736 passed, 34 skipped in 38.32s**. The skips require external data or optional ML
dependencies. Eight new tests cover source-derived chart values, unknown/empty states,
signed deviations and exact precision, HTML escaping, manual refresh and offline metrics.
The existing 116 dashboard tests passed as well.

The first full Windows run exposed two pre-existing guard failures. `.gitattributes` now
keeps the hash-pinned regions file at LF on checkout (the committed JSON is unchanged),
and the evaluation guard normalises path separators before excluding its own package.

Browser command: `node E:\Codex\civicwater-sentinel\browser\validate.cjs` (scratch
Playwright script, installed Chrome). It checked 1536×1080, 390×844, 360×800, 768×1024 and
1440×900; the last used reduced motion. All layouts stayed within the available page
width. Evidence navigation, raw audit/sensor disclosures, history filtering/reset and
manual refresh passed. All local assets loaded; **0 page errors**, **0 external asset
requests**, and **0 GSAP tweens** with reduced motion. A further check of **Source & models**
confirmed that full model names and versions wrap without ellipses. No performance score is claimed.

Review captures from `fixture-cusum`:

- [Desktop overview](images/dashboard-desktop.png)
- [Alert evidence and history](images/dashboard-evidence.png)
- [Mobile overview](images/dashboard-mobile.png)
