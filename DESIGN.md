---
name: CivicWater Sentinel
description: Read-only operator workspace for leak-suspected alerts on the simulated L-Town water network.
colors:
  rail-teal: "#12353a"
  rail-teal-raised: "#285157"
  rail-text: "#eaf3ef"
  rail-text-muted: "#b9d0ca"
  rail-line: "#31565a"
  network-green: "#08796f"
  network-green-ink: "#086d64"
  network-green-tint: "#e4f3ee"
  alert-coral: "#ad443a"
  alert-coral-ink: "#8f3832"
  alert-coral-tint: "#fff0eb"
  notice-amber-wash: "#fbf5e8"
  notice-amber-line: "#eee5cf"
  notice-amber-ink: "#624515"
  simulated-badge-wash: "#f9edc8"
  simulated-badge-ink: "#6b5013"
  canvas-mist: "#f3f7f5"
  map-paper: "#fbfdfc"
  surface-white: "#ffffff"
  deep-ink: "#15343a"
  slate-muted: "#52696a"
  hairline: "#dce8e3"
  pipe-grey-green: "#9eb5ae"
  pipe-faded: "#e1eae6"
  sensor-slate: "#38585b"
  landmark-green: "#2e806e"
  region-ink-1: "#1a73c2"
  region-ink-2: "#312e81"
  region-ink-3: "#9a4bd4"
  region-ink-4: "#b02f86"
  region-ink-5: "#9e6a00"
  region-ink-6: "#831843"
  region-ink-7: "#2f3336"
  region-ink-8: "#6c7a00"
  region-ink-9: "#5b3a1a"
typography:
  display:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.025em"
  evidence-title:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.025em"
  title:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  body:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
  body-small:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.45
  figure:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "-0.03em"
    fontFeature: "tnum"
  data-mono:
    fontFamily: "IBM Plex Mono, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tnum"
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
components:
  pane:
    backgroundColor: "{colors.surface-white}"
    textColor: "{colors.deep-ink}"
    rounded: "{rounded.md}"
    padding: "12px"
  button-primary:
    backgroundColor: "{colors.rail-teal}"
    textColor: "{colors.surface-white}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.rail-teal-raised}"
  button-tool:
    backgroundColor: "{colors.surface-white}"
    textColor: "{colors.deep-ink}"
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "40px"
  button-tool-hover:
    backgroundColor: "{colors.network-green-tint}"
  button-quiet:
    backgroundColor: "{colors.surface-white}"
    textColor: "{colors.network-green-ink}"
    rounded: "{rounded.sm}"
    padding: "2px 10px"
    height: "32px"
  tab:
    backgroundColor: "{colors.map-paper}"
    textColor: "{colors.slate-muted}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "34px"
  tab-selected:
    backgroundColor: "{colors.rail-teal}"
    textColor: "{colors.surface-white}"
  queue-row:
    backgroundColor: "{colors.surface-white}"
    textColor: "{colors.deep-ink}"
    rounded: "{rounded.sm}"
    padding: "9px 8px"
  queue-row-selected:
    backgroundColor: "{colors.alert-coral-tint}"
    textColor: "{colors.alert-coral-ink}"
  simulated-badge:
    backgroundColor: "{colors.simulated-badge-wash}"
    textColor: "{colors.simulated-badge-ink}"
    rounded: "{rounded.xs}"
    padding: "3px 8px"
  notice-strip:
    backgroundColor: "{colors.notice-amber-wash}"
    textColor: "{colors.notice-amber-ink}"
    rounded: "{rounded.sm}"
    padding: "9px 14px"
  mode-chip:
    backgroundColor: "{colors.network-green-tint}"
    textColor: "{colors.network-green-ink}"
    rounded: "{rounded.xs}"
    padding: "4px 8px"
  leak-callout:
    backgroundColor: "{colors.alert-coral}"
    textColor: "{colors.surface-white}"
    rounded: "5px"
    padding: "5px 8px"
  nav-link:
    backgroundColor: "{colors.rail-teal}"
    textColor: "{colors.rail-text}"
    rounded: "7px"
    padding: "10px 12px"
    height: "44px"
  nav-link-selected:
    backgroundColor: "{colors.network-green-tint}"
    textColor: "{colors.network-green-ink}"
---

# Design System: CivicWater Sentinel

## Overview

*Names in this file were derived from the shipped build and PRODUCT.md, not chosen by the owner.*

**Creative North Star: "The Network Is the Workspace"**

A light, quiet operations room around one live object: the L-Town network schematic. A dark teal rail frames a misty paper canvas; white panes sit on it, separated by 1px hairlines rather than shadows. The network itself carries color: teal-green pipes for the region in question, grey-green for the rest, and a single coral voice for anything that deviates. Everything else stays in deep ink and slate so the coral reads instantly.

Density is operator-grade: compact headers, 13-14px supporting text, mono figures with tabular numerals. Nothing decorates; every mark on screen either locates an alert or proves it. Motion exists to move the eye to the alert (a camera flight, a pulse sized by deviation) and stops completely under reduced-motion.

Uncertainty is visible in the material, not hidden in copy: amber washes mark simulated and replayed data, "best guess" sits beside every candidate pipe, and the explanation slot shows a dashed empty state rather than vanishing.

**Key Characteristics:**
- Flat white panes on a mist canvas, 1px hairline borders, no shadows at rest.
- One alert voice (coral); one network voice (teal-green); amber only for data-provenance notices.
- IBM Plex Sans for words, IBM Plex Mono for received values, tabular numerals throughout.
- A dark teal rail for navigation and for the primary action inside the map.
- Fully local assets: Plex faces, Phosphor SVG icons, authored inline SVG in the map.

## Colors

A cool, low-chroma teal world with a single warm alarm color and a muted amber reserved for provenance.

### Primary
- **Network Green** (network-green): the network-context color. Alert-region pipes, links, focus rings (2px outline), the brand mark and the selected nav item's text. Its ink variant (network-green-ink) carries text on the green tint.
- **Network Green Tint** (network-green-tint): selected nav background, mode chip, tool-button hover, text selection.

### Secondary
- **Rail Teal** (rail-teal): the sidebar rail and every primary action inside the map (selected tab, Replay, primary tool). Hover lifts to Rail Teal Raised. Text on the rail uses rail-text and rail-text-muted; dividers on the rail use rail-line.

### Tertiary
- **Alert Coral** (alert-coral): deviating sensors, the candidate pipe and its glow, the leak callout, the score fill and deviation bars, lit replay ticks.
- **Alert Coral Ink** (alert-coral-ink): coral as text on light grounds (evidence region title, selected queue row, candidate pipe ID, sensor tags).
- **Alert Coral Tint** (alert-coral-tint): the selected alert row in the queue.
- **Notice Amber** (notice-amber-wash, notice-amber-line, notice-amber-ink; simulated-badge-wash, simulated-badge-ink): the mandatory provenance notices and the SIMULATED badge, nothing else.

### Neutral
- **Canvas Mist** (canvas-mist): page background behind panes.
- **Map Paper** (map-paper): the map ground, tab tray and disabled-control fill.
- **Surface White** (surface-white): panes, toolbars, floating map panels.
- **Deep Ink** (deep-ink): headings, values, the score threshold mark.
- **Slate Muted** (slate-muted): secondary text, labels, legend keys; also the placeholder color.
- **Hairline** (hairline): every pane border, divider and fact-row rule.
- **Map materials**: pipe-grey-green for pipes outside the alert region, pipe-faded when another region is isolated, sensor-slate for quiet sensors, landmark-green for tanks and reservoirs (square marks).

### Region inks
Nine data inks (region-ink-1 through region-ink-9) color regions in the map's Regions mode only. They hold at least 4.3:1 against Map Paper and deliberately exclude Alert Coral and Network Green, so Regions mode can never be mistaken for an alert.

### Named Rules
**The Coral Is an Alarm Rule.** Alert Coral and its ink and tint mean "this deviates or is suspected", and nothing else. No coral for branding, emphasis, links, destructive buttons or decoration. If a coral mark appears, it must point at an alert value.

**The Amber Means Provenance Rule.** The amber family marks simulated, mock or replayed data. It never signals severity and never disappears while the condition holds.

**The Green Means Network Rule.** Network Green is location and interaction (region, focus, selection). It is not a success state.

## Typography

**Display Font:** IBM Plex Sans (with sans-serif)
**Body Font:** IBM Plex Sans (with sans-serif)
**Label/Mono Font:** IBM Plex Mono (with monospace)

**Character:** An engineering sans with a matching mono: plain, technical, legible at small sizes. Headings tighten their tracking slightly; the mono carries every received number so values line up and read as data, not prose.

### Hierarchy
- **Display** (600, 1.75rem, 1.15, -0.03em): the single page title in the compact header row; 1.5rem below 640px.
- **Headline** (600, 1.5rem, 1.3): below-the-fold section headings (alert detail, history, system status).
- **Evidence Title** (600, 1.375rem, Alert Coral Ink): the selected alert's region at the top of the evidence pane.
- **Title** (600, 1.125rem, -0.02em): pane headings (Recent alerts, Network map) and map panel titles at 16px.
- **Figure** (500, 1.5rem, tabular): score and threshold in the evidence pane.
- **Body** (400, 1rem, 1.6): main text, widget labels, sidebar links, main buttons.
- **Body Small** (400, 0.875rem, 1.5): captions, meta lines, notices, queue summaries; the floor for supporting text in the main shell.
- **Label** (400, 0.8125rem, 1.45): dense pane copy (queue meta, fact rows, map legend and tooltip at 12-13px).
- **Data Mono** (Plex Mono 400, 0.8125-0.875rem, tabular): timestamps, IDs, deviations, scores in tables and fact rows, the replay clock.

### Named Rules
**The Received Values Are Mono Rule.** Any value that came from the source (time, ID, pressure delta, score) is set in Plex Mono or with tabular numerals; prose around it stays in Plex Sans.

**The Sentence Case Rule.** Headings and labels are sentence case. Uppercase is reserved for the mandatory provenance strings (SIMULATED, RECORDED REPLAY) and the badge that carries them.

## Layout

A fluid shell up to 1760px wide on Canvas Mist, with tight outer padding (20px desktop, 12px under 640px) and a 12px gap between header, notice strip and workspace. The first viewport is a three-pane command view: an alert queue fixed at 220px, the map taking the remaining width, and an evidence pane fixed at 260px, all stretched to equal height. The map frame sizes to the viewport (`clamp(420px, 100vh - 162px, 880px)`) so the network dominates at 1280x720 and 1440x900.

Responsiveness is container-driven, not page-driven: under 68rem of content the evidence pane drops below queue and map; under 47rem the panes stack map first, then queue, then evidence, and the map heading becomes visible. Within the map, the toolbar collapses tool labels to icons and the replay clock moves to its own row under 560px. Touch pointers raise every map control to 44px.

Spacing rhythm is small and even: 4/8/12/16/20px steps. Pane padding is 12px (queue, map) to 16px (evidence); dense rows use 8-9px vertical padding; fact rows are divided by hairlines rather than gaps. Interactive targets are at least 44px in the shell and 40px in the map (44px on touch).

## Elevation & Depth

Flat by default. Panes, cards, buttons and the sidebar have no shadow; depth comes from tonal layering (Canvas Mist under Surface White, Map Paper inside panes) and 1px hairlines. The one exception is content that floats over the live map, which needs to separate from a busy drawing.

### Shadow Vocabulary
- **Map float** (`box-shadow: 0 6px 22px rgba(18, 53, 58, .12)`): floating panels over the map only (tooltip, feature detail, replay alert card).
- **Map focus inset** (`box-shadow: inset 0 0 0 2px #08796f`): the map stage's keyboard focus.

### Named Rules
**The Hairline, Not Shadow Rule.** Panes on the canvas separate by a 1px Hairline border. A shadow is earned only by an element floating over the map drawing.

**The Disclosure Is a Row Rule.** Inside a panel, expanders and nested notices are rows divided by a top hairline, never boxes inside a box.

## Shapes

Gently rounded rectangles throughout: 8px for panes, map frames on the full-page map and floating map panels; 6px for buttons, tools, queue rows, the embedded map frame and notice strips; 4px for badges, chips and label tags. Bars (score, deviation) are 8-10px tall with fully rounded ends. Map marks follow their meaning: round sensors, square landmarks, round-capped pipes, a dashed stroke for valves and pumps. Borders are always 1px; the only dashed border in the shell is the empty explanation slot, signalling content not yet available.

## Components

### Buttons
Quiet, compact and rectangular; color comes from the rail, not from the accent.
- **Shape:** gently rounded (6px).
- **Primary:** Rail Teal fill, white text, 40px tall (44px on the full-page map and on touch), 12px horizontal padding. Used for Replay and the selected map tab.
- **Tool:** white with a Hairline border and Deep Ink text and icon; hover fills Network Green Tint; `:active` scales to .97.
- **Quiet text button:** white, 1px soft green border, Network Green Ink text, 32px tall (Refresh in the queue header).
- **Hover / Focus:** 160-180ms color transitions on the expo-out ease; focus is a 2px Network Green outline offset 2-4px. Disabled tools sit on Map Paper with greyed text.

### Chips and badges
- **SIMULATED badge:** amber wash, amber ink, 12px semibold, uppercase with .04em tracking, 4px radius. Sits in the map toolbar and beside the evidence title.
- **Mode chip:** Network Green Tint with Network Green Ink and a 14px icon (RECORDED REPLAY / LIVE in the header).

### Cards / Containers
- **Corner Style:** 8px.
- **Background:** Surface White on Canvas Mist.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Border:** 1px Hairline.
- **Internal Padding:** 12px (queue, map) to 16px (evidence); 16px on mobile for below-the-fold panels.

### Inputs / Fields
Native Streamlit widgets themed through `.streamlit/config.toml` (Network Green primary, Plex faces, medium radius, visible widget borders); text at 1rem, placeholders in Slate Muted. Focus is the shared 2px Network Green outline.

### Navigation
The sidebar is a Rail Teal column (270-300px, collapsed by default) with light rail text. Links are 44px rows with 19px icons; hover fills a lifted teal, the selected link inverts to Network Green Tint with Network Green Ink at weight 550. Dividers and outlined page links use Rail Line. Inside the map, Alert/Regions tabs sit in a Map Paper tray; the selected tab is Rail Teal.

### Alert queue
A native radio group drawn as list rows: region in Deep Ink semibold with time, sensor count and strongest deviation beneath in 13px slate. Rows divide by hairlines; hover is a faint green-grey wash; the selected row fills Alert Coral Tint and its region turns Alert Coral Ink.

### Evidence pane
Region title in Alert Coral Ink with the SIMULATED badge; alert ID in small slate mono; score and threshold as Figures; a score bar (10px track, coral fill, a 2px Deep Ink threshold mark) so the claim reads on one scale; fact rows divided by hairlines with mono values; deviating sensors as short coral bars with signed mono values; a dashed explanation slot; a Network Green link to the full record.

### Network map (signature)
An SVG schematic on Map Paper. Alert-region pipes in Network Green, others in pipe grey-green; deviating sensors in coral with pulse rings scaled by |deviation|; the candidate pipe as a 5px coral stroke with a soft breathing glow and a coral leak callout reading "Suspected leak (best guess)". Labels are small semibold tags on translucent paper. A replay bar under the drawing (Rail Teal Replay button, mono clock, slider with coral tick marks that light as alerts play) and a translucent legend at the lower left. Motion: camera flight to the alert, expo-out ease (`cubic-bezier(.16, 1, .3, 1)`); pulses and flights are removed under reduced motion.

## Do's and Don'ts

### Do:
- **Do** separate panes with a 1px Hairline border on Surface White, with no shadow at rest.
- **Do** reserve Alert Coral for deviating sensors, the candidate pipe, alert scores and the selected alert; use Alert Coral Ink when coral is text.
- **Do** label every candidate pipe "best guess" wherever it is drawn or named, in the same visual weight as the pipe ID.
- **Do** keep SIMULATED and RECORDED REPLAY notices in the amber family and visible in the first viewport whenever they apply.
- **Do** set received values in IBM Plex Mono or tabular numerals.
- **Do** keep every asset local: Plex faces from `dashboard/assets/fonts`, Phosphor SVG or authored inline SVG icons (1.75px outline, filled play/pause), no CDN.
- **Do** make controls at least 44px in the shell and on touch, with the 2px Network Green focus outline.
- **Do** gate every animation behind `prefers-reduced-motion`.

### Don't:
- **Don't** use coral, red or orange for anything that is not an alert value, including buttons, links or brand marks.
- **Don't** use Network Green or coral among region inks; Regions mode must stay visually distinct from Alert mode.
- **Don't** put shadows on panes, cards or buttons; only content floating over the map drawing gets the map float shadow.
- **Don't** nest bordered boxes inside a panel; disclosures and notices inside a panel are hairline rows.
- **Don't** add uppercase kickers, eyebrows or tracked section labels above headings.
- **Don't** introduce a success-green state or "verified"/"confirmed" styling; nothing in this system confirms a leak.
