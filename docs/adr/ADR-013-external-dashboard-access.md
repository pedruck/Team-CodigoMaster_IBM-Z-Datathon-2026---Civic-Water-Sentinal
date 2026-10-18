# ADR-013 — Dashboard hosted off the VM, reading through a read-only gateway or snapshots

Status: Proposed (2026-10-18). Needs the agreement of the VM/API owner (P3) and the dashboard
owner (P6): it changes the rule that nothing on the VM is exposed (Q-OPS-01).

## Context and problem

The dashboard is Streamlit, which cannot run in the VM's s390x container (ADR-009, no `pyarrow`
wheel). The pitch needs a URL a juror can open, so the dashboard has to run on an x86 host, while
the forecaster, detector and audit stay on the LinuxONE. Until now the dashboard was reached
through an SSH tunnel from a team member's laptop and nothing on the VM was public.

## Decision

- The dashboard runs off the VM and reads one of two read-only sources, chosen after the VM
  checks in `docs/external-dashboard.md`:
  1. **Outbound tunnel to a read-only gateway.** `scripts/readonly_gateway.py` listens on
     127.0.0.1 on the VM host and forwards only GET/HEAD `/health`, `/v1/status`,
     `/v1/alerts` (known query keys only), `/v1/alerts/{id}` and `/v1/alerts/{id}/explanation`,
     with a bearer token from a mode-600 file. An outbound tunnel (`ssh -R` to localhost.run,
     or cloudflared) gives it a public HTTPS URL. The API, `/docs`, the audit read and every
     write stay unreachable.
  2. **Snapshots pushed by the VM.** `scripts/push_snapshot.py` uploads the API's own answers
     as one JSON document (`civicwater.dashboard-snapshot/1`) with `PUT` to a pre-signed URL made
     off the VM (`scripts/presign_url.py`). Nothing listens, and no storage credential reaches
     the VM.
- The dashboard keeps one seam (`AlertSource`) and validates every answer with
  `sentinel.schemas`. New adapters: `HttpAlertSource` with a bearer token, `SnapshotAlertSource`
  (the snapshot's age is shown; stale after a configurable age) and `FallbackAlertSource` (a
  recorded run, with its RECORDED REPLAY banner, while the live source does not answer). The
  source is chosen by environment variables, which Streamlit Community Cloud fills from secrets.
- The VM-side scripts use only the standard library and Python ≥ 3.8, so they run on the VM host
  without the project's environment.

## Alternatives

Opening an inbound port on the VM was not considered (firewall and team rule). Exposing the API
itself through the tunnel was refused: it accepts writes and serves `/docs`. A storage token on
the VM for option 3 was refused by the team rule "no token on the VM"; pre-signed URLs expire and
allow one object. cloudflared has no official s390x binary; it stays the second choice for
option 1.

## Consequences and risks

- A public URL exists while the tunnel runs (option 1). Its protection is the token and the
  gateway's allow-list; the token must be rotated if it leaks, and the tunnel turned off after
  the evaluation. The free localhost.run URL changes on every restart.
- Option 3 is up to about one minute behind and stops when the PUT URL expires; the page then
  says how old the snapshot is. A pre-signed GET URL lasts at most seven days.
- If the VM has no outbound access, neither option works: the pitch uses a recorded run and the
  demo video.
- The VM checks are NOT_RUN at the time of writing.

## Dependencies and revisit conditions

Depends on ADR-009 (dashboard boundary) and the API's read contract (§5). Revisit if the VM gets
an approved public endpoint, if Streamlit runs on s390x, or if the API gains its own read-only
authentication.
