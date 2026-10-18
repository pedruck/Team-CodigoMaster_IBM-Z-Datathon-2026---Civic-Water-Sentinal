# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Primary user: a water-utility network operator.** They watch a distribution network for
  suspected leaks, need to know where to look first and why the system thinks so, and must
  never be told a leak is confirmed when it is not.
- **Primary audience of the first impression: the IBM Z Datathon 2026 jury.** They judge the
  product from a 3-minute pitch video. The screen is designed for the operator's job; the
  first viewport must also make the product legible to a jury in seconds.

## Product Purpose

CivicWater Sentinel scores 5-minute SCADA windows from the simulated BattLeDIM L-Town
network on IBM LinuxONE and raises leak-suspected alerts. The dashboard is the operator's
read-only workspace: it shows the issued alerts, where each one points on the network and
the evidence behind it. Success: an operator (or a juror) sees where to look and why within
seconds, and can inspect every received value behind that claim.

## Positioning

Show where to look, and prove why. Every 5 minutes the system scores the SCADA window on IBM
LinuxONE; an alert points to a region, the deviating sensors and a candidate pipe (always
labelled a best guess) on the L-Town network map, with the structured evidence behind it, a
hash-chained audit record, and a plain-language explanation written by IBM Granite running on
the same LinuxONE, checked against the alert's own numbers. It never claims a confirmed leak.

## Operating Context

- Streamlit dashboard (`dashboard/app.py`, multipage `dashboard/pages/`), read-only, talks to
  the backend API (`HttpAlertSource`) or to a recorded run on disk (`PlaybackAlertSource`:
  `run.json`, `status.json`, `alerts.jsonl`). Any playback directory in that format must work
  without code changes.
- Viewed on a laptop through an SSH tunnel to the LinuxONE VM, and screen-recorded for the
  pitch video (1280×720 and 1440×900 are the recording sizes). Must work offline: no CDN,
  no remote fonts or scripts.
- The VM's JupyterLab (s390x, no Streamlit) is where the team tests; an ipywidgets view
  (`dashboard/jupyter.py`, `dashboard/network_map_view.py`) mirrors the main views there.

## Capabilities and Constraints

- Read-only. The dashboard imports only `sentinel.schemas` from the backend and never
  references leak labels or ground truth (ADR-009, `tests/test_dashboard_guard.py`).
- Mandatory notices stay visible: "SIMULATED" when `is_mock`, "RECORDED REPLAY" in playback,
  and "Simulated data (BattLeDIM L-Town). Not validated on a real network."
- The candidate pipe is always a best guess. Unknown region or pipe IDs are reported, never
  guessed. Alerts have no open/closed lifecycle (they are "issued" / "recent").
- "Now" is the simulated clock (`status.last_timestamp`), never the wall clock.
- The Granite explanation exists in the backend (P5) but has no read endpoint in the main
  API yet (Q-AUD-07): the dashboard shows "explanation unavailable" until it does.
- Audit references are shown raw; the UI never says "verified".
- No new dependencies and no CDN; GSAP 3.15 is vendored in `dashboard/assets/vendor/`.

## Brand Commitments

- Name: **CivicWater Sentinel**, Team CodigoMaster, IBM Z Datathon 2026.
- Language: English in the product UI.
- Network source attribution: BattLeDIM 2020 (Vrachimis et al.), CC BY 4.0.

## Evidence on Hand

- Official L-Town topology reduced to `config/l-town-map.json` (909 links, 33 pressure
  sensors, 3 landmarks, 9 regions).
- Playbacks in `dashboard/playback/`: `fixture-cusum` (one mock alert, invented region
  `R-SYN-A`), `fixture-edge-cases`, `fixture-empty`, `fixture-malformed`, and
  `fixture-synthetic-ltown-demo` (hand-authored synthetic alerts with official map IDs and
  invented values, `is_mock: true`, not a detector output).
- A real recorded 2019 run (XGBoost + final CUSUM calibration) does not exist yet; it may
  replace the synthetic demo for the pitch. No real-network validation, accuracy figure or
  customer exists: never fabricate them.

## Product Principles

1. Where to look, then why: location first, evidence one step away, every received value
   inspectable.
2. Honest uncertainty: suspected, best guess, simulated — the interface never upgrades a
   claim the data does not make.
3. Read-only and source-bound: nothing is inferred in the front end; missing stays missing.
4. Demo-ready without special builds: the same screen serves the operator and the recording,
   from any playback in the standard format, offline.

## Accessibility & Inclusion

Keyboard operable, WCAG AA contrast, `prefers-reduced-motion` respected, no horizontal page
scroll from 360 px to 1920 px, readable with root text enlarged to 200%.
