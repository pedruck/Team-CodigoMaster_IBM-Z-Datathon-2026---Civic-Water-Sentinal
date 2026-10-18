# Dashboard UX review — BE-021 / BE-023 / BE-024

## Interactive map — BE-024 (2026-10-18), reviewed with Impeccable

Process: `init` (PRODUCT.md), `shape` (three structures on the decision page; the owner chose
the three-pane command view), build, then `critique` as two isolated assessments (design review;
detector plus browser overlay), `audit` (technical checks in Chromium), `polish`, a second
detector run and the finish review. The incumbent visual world was kept.

| Finding | Source | Correction | Verification |
|---|---|---|---|
| Sensor labels covered the candidate pipe, so the "best guess" callout pointed at a dot | critique A (P1) | Labels are laid out in priority order and avoid each other and the pipe; the camera frames the pipe and its deviating sensors | Captures at three sizes |
| During replay the map showed another alert than the heading and evidence pane | critique A (P1) | In-map note while another alert is lit; the card names the lit alert; Regions mode sets the card aside | Browser check: card, note and "Back to selected alert" |
| Queue row styling did not apply (selector expected direct children) | critique A (P1) | Descendant selector; selected row tinted, hover and focus ring | Computed background of the checked row |
| Map stage only 341 px tall at 1280 × 720 | critique A (P1), finish review | Header beside the sidebar toggle, one-line notice and caption, zoom stack and legend drawn over the map, short credit (full attribution in the footer) | Frame 709 × 558, stage 707 × 454 (asked: about 480) |
| Mandatory notice text crossed its box border | critique A (P2), audit | Streamlit's negative markdown margin removed inside notices | scrollHeight = clientHeight |
| SIMULATED repeated in four styles; developer words ("is_mock") in the notice | critique A | Plain wording; the replay card no longer repeats the map's badge | Text review |
| Legend and detail said "Below expected" whatever the sign | critique A | "Deviating sensor"; detail note follows the sign | Code review |
| Fit icon read as full screen; shortcuts undiscoverable | critique A | "Whole network" with its own icon; tooltips name the keys | Visual review |
| Collapsed sidebar links took six Tab stops | critique A | Sidebar content is `inert` while collapsed | `inert` read back in Chromium |
| Map controls 36 px | audit | 40 px, 44 px on coarse pointers | Measured |
| "best guess" at 10.8 px | audit | 12 px | Measured |
| `side-tab` (4 px left border) and card-in-card notice/expanders | detector | Dead feed CSS removed; disclosures and the detail notice drawn flat inside panels | Detector CLI `[]`; overlay clean on the top page at 1440 |
| Placeholder text 3.7:1 in history filters | detector overlay | Placeholder ink #52696a | Overlay |
| Score and threshold were two bare numbers | critique A question | One-scale bar with the threshold marked and a one-line reading | Visual review |

Finish review (`impeccable-finish-reviewer`): disposition **fix**, eight material fixes, all
applied: the replay note moved into the card and the camera frames the lit pipe clear of it;
the card shows SIMULATED for mock alerts; the larger stage above; the recording note (for the
demo, "SYNTHETIC, hand-authored, not a detector output") follows the fixed caption on the page;
the sidebar "DATA SOURCE" eyebrow removed; mobile label collisions and the orphan legend item
fixed; shorter score label and a full-width selected queue row; two region inks that read alike
replaced. Moving the zoom controls into the map exposed a defect the browser check caught: the
stage captured their clicks. Overlay controls now keep their own pointer and key events.

Remaining detector notes judged false positives: `text-occlusion` on the map's label layer
(labels are drawn above the SVG with `pointer-events: none`, so hit-testing reaches the SVG) and
`low-contrast` on Streamlit's slider value on the map page (the value sits on the page ground).

Measured contrast (Chromium): map text 4.6–13.3:1, workspace text 5.4–7.6:1. Reduced motion:
no running animations and an instant camera. Touch (emulated, Chromium): tap opens details;
pinch was not synthesised. Not assessed: spoken screen-reader output, a real touch device,
200% text on the new panes, JupyterLab on the VM.

## Responsive follow-up — 2026-10-18

The owner's report exposed gaps in the earlier checks, which had not exercised sidebar
collapse at tablet widths or enlarged sidebar text. The UI/UX Pro Max responsive/overflow
guidance was reapplied to the existing Streamlit stack. The targeted search returned web
guidance on internal scrolling, avoiding clipped content and unobscured navigation.

| Observed defect | Correction | Verification |
|---|---|---|
| A hidden sidebar reserved 270 px at 768 px viewport width. | Scope width limits to the expanded state and retain native collapse behaviour. | Main starts at 0–1 px after collapse at seven widths; both states checked. |
| Low-contrast collapse icon and accumulated spacing hid the footer. | Visible toggle/icon colours, 44 px controls and consistent sidebar gaps. | Open sidebar screenshots reviewed; footer reachable at normal and 200% text. |
| Native widgets referenced removed Geist fonts. | Align native body/heading/code families with local IBM Plex assets. | Browser-computed font families checked for headings, body and buttons. |
| Negative native heading margins shifted controls off their centre line. | Reset scoped heading margins, stretch buttons and reflow rows by content width. | Shared heading/button centre lines within 2 px; mobile actions stack at full available width. |
| Enlarged replay/map/footer labels clipped or overflowed. | Allow wrapping, scale avatar boxes with text and remove flex minimum-size constraints. | 200% text with no page/sidebar horizontal overflow at 360–1920 px. |
| Map section was omitted from current-section tracking; mobile navigation covered the destination. | Derive tracked headings from the links; dismiss the mobile drawer after selection and focus the heading. | Browser checks verify the current map link, destination focus and drawer dismissal. |

The reusable checker and exact results are in [design verification](dashboard-design.md#responsive-repair-verification--be-023-2026-10-18).
All original evidence, fields, notices, filters, pagination and polling remain. The earlier
review below is retained as the record of BE-021.

## Original review — 2026-10-17

Reviewed on 2026-10-17 against the owner's request: enlarge the interface, make issued
alerts the first operational information, and simplify use without removing information.
The initial visual direction was approved; this pass keeps that identity and corrects the
task flow.

## Findings and implemented changes

| Finding in the initial redesign | Decision in this refinement | Verification |
|---|---|---|
| Four metrics and two technical diagrams preceded the alert list. | Order the page as issued alerts → evidence → history → expandable system status. Errors and mandatory simulation/replay notices remain visible. | AppTest checks ordering; the first alert summary appears in the first viewport at all five tested sizes. |
| Supporting labels were roughly 9–12 px; the initial alert table required scanning many columns. | Main alert copy and controls use 16 px; supporting text uses at least 14 px; region headings use 22 px. Show the newest five summaries with region, issued time, sensor count, strongest signed difference and simulated age. | Browser-computed font sizes checked on desktop and mobile; seven-record test confirms all records remain available. |
| Simplifying a table could accidentally remove evidence or imply a lifecycle. | Retain every loaded row and field in **All alert fields**, the existing detail selector, exact-value disclosures and filtered history. Say “issued” or “recent”; do not invent severity, open/closed status or a resolve action. | Existing contract tests and new missing-data, escaping and disclosure tests pass. |
| Main sections skipped from h1 to h3; clicking evidence needed an obvious destination. | Use h1 → h2 → h3. **View evidence** selects the clicked record, scrolls to detail and focuses its heading after an explicit action. Automatic polling does not request that focus. | Keyboard Enter reaches `alert-detail`; focus remains there through a 5.5-second polling interval. Visible heading levels checked in Chrome. |
| The recent count lacked a contextual announcement. | Give the complete “N recent alerts” phrase one atomic status region. | Semantic markup tested; spoken announcements with assistive technology remain a manual check. |
| The warning text measured 4.42:1; native caption opacity weakened supporting text. Some chart bars were below 3:1. | Darken warning text and chart colours; remove native caption opacity in the workspace/sidebar; keep direct signed value labels. | Warning text 8.12:1; primary-feed text samples ≥5.03:1; caption sample 5.90:1. Green bars 3.85:1, amber bars 4.00:1, zero line 3.11:1 against white. |
| Wide control rows and unbroken labels did not accommodate enlarged text well. | Stack controls/evidence by available content width; let tags and buttons wrap. Keep a 44 px minimum height for main native buttons. | No horizontal page overflow at the tested viewport sizes or with root text enlarged to 200% at 1536 and 390 px. Wide data tables retain their own scrolling. |

System status still contains all four metrics, the processing-stage p95 profile and the
contiguous history buffer. **Source & models** still shows complete model identities.
Simulation, recorded playback, provisional sensors, unavailable data, contract mismatches
and stale-data warnings keep their existing meanings. The source adapters, schemas,
polling/back-off rules and Jupyter renderer are unchanged.

## How the requested skill was applied

Reference: [UI/UX Pro Max SKILL.md](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/blob/56ea9cf174ed86786187daa9de0e6247cb0747e4/.claude/skills/ui-ux-pro-max/SKILL.md),
read with its `references/quick-reference.md` and `references/pro-rules.md`, pinned at
`56ea9cf174ed86786187daa9de0e6247cb0747e4`. The skill was read from an isolated checkout;
it was not installed as an application dependency.

This is a targeted revision of an existing **Streamlit** dashboard. Streamlit is not a
supported stack in the skill's search tool, so only applicable web UX guidance was used.
No React, native-app or marketing-page layout was substituted for the existing runtime.
The approved local Geist/Phosphor palette and reduced-motion-aware GSAP enhancement stay.

Searches used the skill's Python script by absolute path with `--domain ux`:

| Query | Applicable result / interpretation |
|---|---|
| `dashboard primary task hierarchy` | Heading Hierarchy and Font Scale applied; the priority of alerts comes from the owner, not from a search result. |
| `body text readability contrast` | Contrast Readability and Color Contrast informed measurements and typography. |
| `touch target size` | Distinguish the web 24 CSS px AA criterion from native platform units. The project chooses 44 CSS px main-button height for comfortable use. |
| `keyboard navigation` | Keyboard Navigation informs native controls and focus checks. |
| `focus navigation dynamic content` → `keyboard focus` | The first result was too generic; the narrower keyboard guidance was used with an explicit-action focus check. |
| `live badge count screen reader` | Contextual Live Badge Updates informs one complete count phrase. |
| `progressive disclosure complexity` → `progressive disclosure` | Neither database result was a verified match. The disclosure decision instead uses the skill's explicit quick-reference §8 guidance and the owner's request. |

Recommendations were checked against actual contracts and repository rules. The skill
does not justify hiding mandatory notices, adding invented data or replacing the
operator's primary task with a decorative visual pattern.

## Validation and limits

- Full Python gates: **138 files formatted**, ruff clean, strict mypy clean for **137 files**;
  **741 passed, 34 skipped in 42.36s**. The skips need external data or optional ML packages.
- Chrome/Playwright: **1536×1080, 390×844, 360×800, 768×1024, 1440×900**; first alert
  visible, 16/14 px alert text, collapsed system status, evidence selection, audit disclosure,
  history filter/reset and refresh passed. Zero page errors or external requests; zero
  GSAP tweens under reduced motion.
- Additional browser checks: primary-feed contrast including inherited opacity, keyboard
  activation/focus, heading order, 44 px evidence-button height and 200% root text passed
  at desktop/mobile sizes. These are focused checks, not a claim of full WCAG conformance.
- The browser preview uses the committed, clearly labelled synthetic `fixture-cusum`
  recording. No real-network detection or live VM integration is claimed by this review.

Exact gate commands and the browser commands are recorded in
[dashboard-design.md](dashboard-design.md#verification--2026-10-17).
Review captures: [issued alerts](images/dashboard-desktop.png),
[evidence](images/dashboard-evidence.png), [mobile](images/dashboard-mobile.png).

## Remaining work

The code changes above are implemented. Next is the owner's local visual validation and a
short operator rehearsal: identify the newest alert, explain where/when it was issued,
open its evidence and find an older record. Timing and comprehension have not been tested
with an operator, so no usability improvement percentage is claimed. Screen-reader output
also needs manual confirmation.

Pressure/residual history, audit verification, explanations and alert lifecycle actions
remain dependent on their existing API contracts. They are separate product work; this
revision does not manufacture them to fill the interface.
