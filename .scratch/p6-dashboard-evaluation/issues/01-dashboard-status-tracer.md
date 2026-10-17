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

**Status:** done (2026-10-17)

- [x] Streamlit and the tunnel verified; outcome recorded in the ticket's Comments
- [x] The dashboard dependency group exists; the base `uv sync` is unchanged
- [x] The header shows simulated time, model infos, the SIMULATED badge (when `mocks_active`) and online/offline
- [x] The HTTP adapter returns parsed contract models; typed errors for mismatch and unavailable, covered by tests
- [x] The label-guard test is green and part of `just check`
- [x] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`

## Comments

- 2026-10-17 — Streamlit 1.66.0 (pyarrow 26.0.0) installs on x86 in ~8 s. SSH works with
  `linux1@148.100.99.103` (host `ibmdatathon`, s390x); the runbook's `linuxone` is a local
  ssh-config alias. Tunnel `ssh -f -N -o ControlMaster=no -o ControlPath=none
  -o ExitOnForwardFailure=yes -L 8000:172.17.0.2:8000 linux1@148.100.99.103` works; real
  `/health`, `/v1/status`, `/v1/alerts` parse with the contract models. The shared API had
  replayed all of 2019 with mocks: 0 alerts.
- Runbook issue for P3: `docker inspect -f '{{.NetworkSettings.IPAddress}}' notebook` fails on
  the VM (`map has no entry for key "IPAddress"`); `172.17.0.2` still valid. Goes to UI-006.
- `just` is not installed on the P6 laptop; gates were run with the equivalent `uv run`
  commands.
