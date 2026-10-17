# ADR-009 — Operator dashboard: server-side, contract-checked, read-only

- **Status:** Proposed — author: P6 (dashboard & evaluation). Needs confirmation by the
  backend owner P3 (read API, Q-UI-07, Q-DOC-01/02), the detection owner P4 (Q-UI-07,
  Q-DET-07) and the audit owner P5 (Q-AUD-06, Q-AUD-07).
- **Date:** 2026-10-17
- **Implementation:** `dashboard/` (UI-001..005 done),
  `tests/test_dashboard_*.py`. Spec: `.scratch/p6-dashboard-evaluation/spec.md`.

## Context
The read API (contract §5) serves `/health`, `/v1/status`, `/v1/alerts` and
`/v1/alerts/{id}`. Nobody can see its output. The demo deadline is 2026-10-18 10:00; the
forecaster and detector may still be mocks, and the VM, the models or the LLM may fail on
the day. Alerts are only emitted when they open and never close (Q-DET-04; answered that way
on P4's branch). State is in memory and lost on restart (ADR-003). The API runs on the
LinuxONE VM inside a container reached through an SSH tunnel (`docs/runbook-vm.md`).

Numbering: ADR-007 (CUSUM detector) and ADR-008 (calibration and evaluation protocol) are
used by the detection owner (BE-012, merged in PR #5); this ADR takes
the next free number.

## Problem
How to show alerts and system status to an operator and a jury without hiding mocks or
replays, without breaking silently when the API drifts from the contract, and without
coupling the dashboard to backend internals.

## Decision
1. **Server-side Python dashboard (Streamlit)** in `dashboard/`, dependency in the optional
   `dashboard` dependency group (`just dashboard`, `just dashboard-playback`). The VM runtime
   install is unaffected. The browser talks only to Streamlit; Streamlit calls the API.
2. **One data-source seam, `AlertSource`** (`dashboard/source.py`): `list_alerts`,
   `get_alert`, `status`, `health`. Two adapters: `HttpAlertSource` (base URL from config)
   and `PlaybackAlertSource` (a recorded run on disk: `run.json`, `status.json`,
   `alerts.jsonl`, same filtering/ordering/pagination as the API).
3. **Runtime contract parsing.** Every response body is parsed with the `sentinel.schemas`
   models. A parse failure raises `ContractMismatchError` and the screen shows a "contract
   mismatch" state, never partial or guessed data. An unreachable backend raises
   `SourceUnavailableError`.
4. **Dependency boundary.** The dashboard imports only `sentinel.schemas` from the backend
   package (never services, ports or models) and never references leak labels; both are
   enforced by `tests/test_dashboard_guard.py`.
5. **Simulated clock.** "Now" is `status.last_timestamp` (end of the last accepted window,
   contract §1), never the browser or server clock. Alert age = that minus `window_end`.
   `created_at` (server clock) appears only in the detail view, labelled.
6. **"Recent alerts", not "active alerts":** alerts never close (Q-DET-04), so no open/closed
   state is inferred in the front end. An "open" filter is added only if a lifecycle field
   is added to the contract (Q-UI-05 on P4's branch).
7. **Refresh by polling** every ~5 s (configurable), exponential back-off up to 60 s on error,
   liveness from `/health`; new alerts fetched with `since` = newest `window_end` shown,
   deduplicated by `alert_id` (`since` is inclusive). No SSE/WebSocket. When the simulated time or the listed alerts change, the whole page reruns so the header clock and the detail selector follow the list (UI-005).
8. **Mandatory banners:** "SIMULATED" when `status.mocks_active` or `alert.is_mock`;
   "RECORDED REPLAY — run <id>, <date>" whenever the playback adapter is active; "Provisional
   sensor IDs" when the registry is provisional; a fixed "Simulated data (BattLeDIM L-Town),
   not validated on a real network" caption.
9. **Audit shown raw.** `audit.seq` / `audit.hash` as received, or "audit unavailable" when
   null. The UI never shows "verified": verification waits for the audit read contract
   (Q-AUD-06), and the backend will not supply a `verified` boolean.
10. **Explanation slot** shows "explanation unavailable" until Q-AUD-07 exists; the detail
    view shows structured evidence only.
11. **No screens for endpoints that do not exist** (residuals Q-UI-07, audit history
    Q-AUD-06, narrative Q-AUD-07, BattLeDIM score Q-DET-07): they are requested from their
    owners, not worked around.

**Addendum (2026-10-17) — Jupyter view.** Streamlit cannot run in the VM's JupyterLab
container: `pyarrow` has no s390x wheel on PyPI (sdist only), `jupyter-server-proxy` is absent and
only the Jupyter port is published. A second renderer, `dashboard/jupyter.py` (ipywidgets, already
in the container), reuses the same seam, poll, formatting and banners; the notebook
`notebooks/p6_eval/dashboard.ipynb` mounts it. The Streamlit page stays the primary view (laptop +
SSH tunnel); the Jupyter view has no history filters.

## Alternatives considered
- **React SPA with types generated from OpenAPI:** better UX, but a second toolchain, CORS
  or a proxy, and generated types that still do not validate responses at runtime; too much
  for one day.
- **Static HTML/JS served by FastAPI:** no tunnel to a second process and no CORS, but needs
  a P3 change in the API and duplicates the contract in JavaScript. Kept as the fallback if
  Streamlit cannot be run on the demo machine.
- **Server-Sent Events / WebSocket push:** lower latency, but a new API surface owned by P3;
  5-minute windows replayed at speed do not need sub-second push.

## Pros
One language and one contract source (the Pydantic models); drift is visible at runtime;
playback is both the demo contingency and the test fixture source; no backend change.

## Cons
Streamlit reruns the script on each interaction (coarse control over refresh); polling adds
load proportional to the number of open dashboards; offset pagination can shift while new
alerts arrive (anchoring on `until` is technical debt).

## Consequences
- No CORS in the API (Q-UI-03 answered, DEP-UI-02 resolved).
- Any contract change in `sentinel.schemas` reaches the dashboard immediately; an
  incompatible change shows "contract mismatch" until the dashboard is updated.
- After a backend restart the dashboard shows what the new process has (ADR-003): older
  alerts disappear; this is shown, not hidden.

## Risks
- **Streamlit on s390x is unverified** → the dashboard runs on an x86 laptop and reaches the
  VM API through the SSH tunnel (verified in the UI-001 smoke, `docs/status.md`).
- **In-memory API state:** a restart during the demo empties the alert list → playback of a
  recorded run, always under the RECORDED REPLAY banner.
- A replay presented as live, or a mock presented as detection → mitigated only by the
  mandatory banners; they must never be made optional.
- Tunnel address changes when the container is recreated; the runbook command to find it is
  broken (Q-DOC-02).

## Dependencies
Read API (contract §5, P3); Q-UI-07 (P3 + P4), Q-AUD-06 and Q-AUD-07 (P5 + P3), Q-DET-07
(P4, P1), Q-DOC-01/02 (P3) in `docs/questions-for-team.md`; DEP-UI-03, DEP-AUD-02,
DEP-AUD-04 in `docs/dependencies.md`.

## Revisit when
The API adds residuals, audit read, explanation or an alert lifecycle; Streamlit is verified
on s390x; the API state becomes durable; or the dashboard must serve more than a few
simultaneous viewers.
