# 01: UI-001 — Tracer: dashboard shows the live API status

**What to build:** The P6 owner runs a single `just` recipe and a Streamlit page opens showing a header built from the live backend:
- the current **simulated time** (the status response's last accepted timestamp);
- the forecaster and detector name and version;
- a prominent **SIMULATED** badge when mocks are active;
- an online/offline indicator from the health endpoint.

It works against the VM API through the SSH tunnel documented in the VM runbook.

This ticket introduces the dashboard's only seam, the **alert source**, with its first adapter (**HTTP**). Every response is parsed with the shared contract models; a parse failure is a typed "contract mismatch" error and an unreachable backend is a typed "unavailable" error. The dashboard imports only the contract models from the backend package, never services or model code. Streamlit goes in a new optional dependency group, so the VM runtime install is unaffected.

It also adds the CI guard: a test that fails if dashboard sources reference leak labels or ground-truth files.

**First 10 minutes:** verify that Streamlit installs on x86 and that the tunnel reaches the VM API. If either fails, stop and report; the fallback (static page served from the API origin) needs a P3 decision.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Streamlit and the tunnel verified; outcome recorded in the ticket's Comments
- [ ] The dashboard dependency group exists; the base `uv sync` is unchanged
- [ ] The header shows simulated time, model infos, the SIMULATED badge (when `mocks_active`) and online/offline
- [ ] The HTTP adapter returns parsed contract models; typed errors for mismatch and unavailable, covered by tests
- [ ] The label-guard test is green and part of `just check`
- [ ] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
