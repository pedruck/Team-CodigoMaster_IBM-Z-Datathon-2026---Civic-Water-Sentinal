# Dashboard hosted off the VM (BE-026)

The Streamlit dashboard cannot run on the VM's s390x container (no `pyarrow` wheel, ADR-009),
and the pitch needs a public URL. The dashboard therefore runs on an x86 host (laptop,
Streamlit Community Cloud), while the forecaster, detector and audit stay on the LinuxONE. This
document covers how the dashboard reaches the data, the checks that decide the option, and the
rules for using it. Decision record: [ADR-013](adr/ADR-013-external-dashboard-access.md).

**Status:** code and tests on branch `feat/be-026-external-dashboard`. VM checks from the
Jupyter terminal (container, 2026-10-18): s390x, Python 3.12.15, API `/health` 200, outbound
HTTPS open (GitHub, localhost.run, Cloudflare, Google Storage, S3), outbound TCP 22 open and
localhost.run answering, `snapshot-read` JSON ok. Tunnel test (1a) from the Jupyter terminal:
the `https://….lhr.life` URL served the test page (`ok`, HTTP 200, valid TLS, 0.8 s) to a phone
off the Wi-Fi and to an x86 host outside the VM, and `/v1/status` through it was 404 (only the
test page is behind it). It stayed up for about 10 minutes and was then stopped on purpose; the
30-minute criterion is **not verified**. Snapshot upload (3b/3c): NOT_RUN. Nothing is exposed
until the team agrees (Q-OPS-01).

## The two options

| | Option 1: outbound tunnel | Option 3: the VM pushes snapshots |
|---|---|---|
| How | The VM opens an outbound tunnel (`ssh -R` to localhost.run, or cloudflared) to `scripts/readonly_gateway.py`, which forwards a fixed set of GET requests to the API. | Every ~60 s, `scripts/push_snapshot.py` reads `/health`, `/v1/status` and `/v1/alerts` and uploads one JSON document with `PUT` to a pre-signed URL. |
| Freshness | live | about one minute behind |
| If the VM is down | no data; the dashboard can fall back to a recorded run | the dashboard shows the last snapshot and its age |
| What is exposed | a public HTTPS URL to the read-only gateway, protected by a bearer token | nothing listens; the VM only writes one object to storage |
| Secrets on the VM | the gateway token (file, mode 600) | none stored: the pre-signed URL is typed without echo and expires |
| Dashboard setting | `SENTINEL_DASHBOARD_API_URL` + `SENTINEL_DASHBOARD_API_TOKEN` | `SENTINEL_DASHBOARD_SNAPSHOT_URL` (a pre-signed GET URL) |

In both cases `SENTINEL_DASHBOARD_FALLBACK_DIR` can point at a recorded run (e.g.
`dashboard/playback/fixture-synthetic-ltown-demo`). While the live source does not answer, the
page shows that recording with its RECORDED REPLAY banner, plus "LIVE SOURCE UNAVAILABLE"; it
switches back on the next successful check.

## Components

| File | Runs on | What it does |
|---|---|---|
| `scripts/readonly_gateway.py` | VM host | Listens on 127.0.0.1 only. Forwards GET/HEAD `/health`, `/v1/status`, `/v1/alerts` (only `limit`, `offset`, `since`, `until`, `region`), `/v1/alerts/{id}` and `/v1/alerts/{id}/explanation`. Everything else gets 404 without reaching the API (no POST, no `/docs`, no `/openapi.json`, no audit read). Every request needs `Authorization: Bearer <token>`, compared in constant time; the token file must be mode 600 and at least 24 characters. Five requests per second per client, no redirects followed, answers capped at 8 MB, the log never contains headers. Stdlib only, Python ≥ 3.8. |
| `scripts/push_snapshot.py` | VM host | Builds `{"schema": "civicwater.dashboard-snapshot/1", "taken_at", "health", "status", "alerts"}` from the API's own answers and `PUT`s it. The URL comes from `SNAPSHOT_PUT_URL` or is typed without echo; it is refused as an argument and never printed with its signature. If the API does not answer, nothing is uploaded, so the stored snapshot keeps its `taken_at`. Stdlib only, Python ≥ 3.8. |
| `scripts/presign_url.py` | your computer | Makes a pre-signed S3-compatible URL (AWS S3, Cloudflare R2, Google Cloud Storage with HMAC keys) with AWS Signature V4. `PUT` for the VM (e.g. 2 h), `GET` for the dashboard (at most 7 days). The storage credentials stay on your computer. Checked against the AWS documentation example. |
| `scripts/external_dashboard_checks.sh` | VM host | The plan's checks, one subcommand per step (below). Stores no secret. |
| `dashboard/source.py` | dashboard host | `HttpAlertSource(token=…)`, `SnapshotAlertSource` (URL or file, notice with the snapshot's age, "SNAPSHOT IS … OLD" after `SENTINEL_DASHBOARD_SNAPSHOT_MAX_AGE_S`, default 300) and `FallbackAlertSource`. Every answer is still validated with `sentinel.schemas`. |

## Checks on the VM

Run on the **host** (`ssh linuxone`), not in the Jupyter terminal, and announce it to the team
first. `API` defaults to `http://172.17.0.2:8000` (the shared API as the host sees it).

```bash
cd ~/shared/civicwater/main      # or your own clone
scripts/external_dashboard_checks.sh prereq          # step 0
scripts/external_dashboard_checks.sh tunnel-test     # option 1a: serves a page that says "ok"
scripts/external_dashboard_checks.sh cloudflared     # option 1b: only if 1a fails
scripts/external_dashboard_checks.sh snapshot-read   # option 3a
scripts/external_dashboard_checks.sh snapshot-put    # option 3b: needs a pre-signed PUT URL
scripts/external_dashboard_checks.sh snapshot-loop   # option 3c: 15 PUTs, one a minute
```

| Step | Pass when |
|---|---|
| prereq | `s390x`; Python ≥ 3.8; API `/health` 200; at least one outbound HTTPS code other than `000`; outbound ssh "open (localhost.run answered)". localhost.run rejects any command with exit 255 even when the connection works, so the script reads its banner instead. All `000` and ssh blocked means the VM has no way out: use the recorded run and the demo video. |
| tunnel-test | The printed `https://….lhr.life` URL shows `ok` on a phone **off the Wi-Fi** and stays up for 30 minutes. It serves a test page, never the API. |
| cloudflared | `200` means an official s390x binary exists; `404` means it would have to be built with Go off the VM. |
| snapshot-read | `JSON ok`. |
| snapshot-put | `200`, and the object can be read from your computer. |
| snapshot-loop | 15 lines of `200`, and `taken_at` in the stored object keeps changing. |

Decision: 1a passes → option 1 with ssh; 1a fails and 1b passes → option 1 with cloudflared;
tunnels fail and 3b/3c pass → option 3; nothing passes → recorded run plus video.

## Using option 1

```bash
# on the VM host, once: a token only the owner can read (outside every git clone)
umask 077 && python3 -c "import secrets;print(secrets.token_urlsafe(32))" \
    > ~/shared/civicwater/secrets/dashboard-gateway.token
# in tmux: the gateway, then the tunnel to it
python3 scripts/readonly_gateway.py --upstream http://172.17.0.2:8000 \
    --token-file ~/shared/civicwater/secrets/dashboard-gateway.token --port 8099
ssh -o ServerAliveInterval=30 -R 80:127.0.0.1:8099 nokey@localhost.run
```

Dashboard secrets: `SENTINEL_DASHBOARD_API_URL = "https://….lhr.life"`,
`SENTINEL_DASHBOARD_API_TOKEN = "<token>"`, optionally
`SENTINEL_DASHBOARD_FALLBACK_DIR = "dashboard/playback/<recorded run>"`. The free localhost.run
URL changes when the tunnel restarts; update the secret then.

## Using option 3

```bash
# on your computer: storage credentials stay here
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
python scripts/presign_url.py PUT --endpoint https://<account>.r2.cloudflarestorage.com \
    --bucket civicwater --key snapshot.json --expires 7200        # paste into the VM
python scripts/presign_url.py GET --endpoint … --bucket civicwater --key snapshot.json \
    --expires 604800                                              # dashboard secret
# on the VM host, in tmux
read -rs SNAPSHOT_PUT_URL && export SNAPSHOT_PUT_URL
python3 scripts/push_snapshot.py --api http://172.17.0.2:8000 --every 60 --count 0
```

Dashboard secret: `SENTINEL_DASHBOARD_SNAPSHOT_URL = "<pre-signed GET URL>"`. A new PUT URL is
needed when the old one expires; the dashboard then shows the snapshot's age until it resumes.

## Hosting on Render (BE-028)

`render.yaml` at the repo root is a Render Blueprint for the dashboard with option 1: one free
Python web service, built with `uv sync --locked --no-dev --group dashboard` (Render adds uv
because `uv.lock` is at the root), started with Streamlit on `$PORT`, health check
`/_stcore/health`, `autoDeploy: false` so the demo URL only changes when someone deploys on
purpose. Python 3.12.13 (`PYTHON_VERSION`), like CI.

1. On the VM host (`ssh linuxone`), start the gateway as in "Using option 1" and the tunnel
   below (two tmux sessions). Note the tunnel's URL.
2. Render dashboard → **New → Blueprint** → this repository (as a public Git repository URL),
   branch `main` → it reads `render.yaml` and asks for the two `sync: false` values:
   - `SENTINEL_DASHBOARD_API_URL`: the tunnel's URL;
   - `SENTINEL_DASHBOARD_API_TOKEN`: the content of
     `~/shared/civicwater/secrets/dashboard-gateway.token` (copy it yourself; never paste it in
     chat, an issue or git).
3. After the deploy, the page header should read CONNECTED with "Latest simulated window"
   2019-12-31 23:55 UTC. If it shows RECORDED REPLAY + "LIVE SOURCE UNAVAILABLE", the tunnel or
   the token is wrong: the synthetic recording is on screen instead.

**Tunnel: Cloudflare quick tunnel, not localhost.run.** The free localhost.run domain rotates by
itself every few minutes (observed 2026-10-18: `cw-tunnel` changed domain within about ten
minutes, with the process still running), so the URL stored in Render died and the page fell
back to the recording ("LIVE SOURCE UNAVAILABLE"). A `cloudflared` quick tunnel keeps its
`https://….trycloudflare.com` URL for the life of the process, needs no account and has no
request quota (no uptime guarantee either). Cloudflare publishes no s390x binary, so it is
built from the official source off the VM and copied to the VM host (outside every clone):

```bash
# on an x86 computer with Go ≥ the version in cloudflared's go.mod
git clone --depth 1 --branch 2026.10.1 https://github.com/cloudflare/cloudflared.git
cd cloudflared && CGO_ENABLED=0 GOOS=linux GOARCH=s390x go build -mod=mod -trimpath \
    -ldflags "-s -w -X main.Version=2026.10.1" -o cloudflared-linux-s390x ./cmd/cloudflared
scp cloudflared-linux-s390x linuxone:shared/civicwater/users/<you>/bin/cloudflared
# on the VM host, in tmux (session cf-tunnel): the URL is printed in the log
~/shared/civicwater/users/<you>/bin/cloudflared tunnel --no-autoupdate \
    --url http://127.0.0.1:8099 2>&1 | tee ~/cf-tunnel.log
```

When the tunnel process restarts, its URL changes: update `SENTINEL_DASHBOARD_API_URL` in Render
(Environment → Edit → Save only, then Manual Deploy → Deploy latest commit). The gateway reads its
token file once, at start: after regenerating the token, restart the gateway too, or every request
gets 401.

**Keep-alive.** The free plan sleeps after 15 minutes without requests and takes about a minute
to wake (27 s measured on the first visit). `.github/workflows/keep-dashboard-awake.yml` pings
`/_stcore/health` every 5 minutes (repository variable `DASHBOARD_URL` overrides the URL); it
never calls the API. A web page (e.g. GitHub Pages) would only ping while someone has it open.
One always-on free service uses about 744 of the workspace's 750 free instance hours a month;
disable the workflow after the event. Verified before
the first deploy (x86-64, 2026-10-18): the build command and the start command above, run
locally with `PORT=8510` against the shared API through an SSH tunnel: health `ok`, the page
CONNECTED with the 2019 replay, ~77 MB resident.

**Deployed 2026-10-18:** https://civicwater-sentinel-dashboard.onrender.com (Blueprint
`civicwater-sentinel`, service `civicwater-sentinel-dashboard`, free plan, from `main` @
`84611b4`). Through the tunnel with the token: `/health`, `/v1/status`, `/v1/alerts` 200,
`/docs` and `/openapi.json` 404, no token 401. The hosted page read CONNECTED, latest simulated
window 2019-12-31 23:55 UTC, the queue with dates, score `170.01` vs `128.0` and the stored
explanation.

## Rules (either option)

- Expose only the read-only gateway (GET on status and alerts). Never the API, Jupyter or `/docs`.
- Tokens and URLs live outside git, in the dashboard host's secrets.
- Turn the tunnel or the loop off after the evaluation.
- This changes the rule that nothing is exposed: the VM/API owner (P3) and the dashboard owner
  (P6) must agree first (Q-OPS-01 in `docs/questions-for-team.md`).
- The data stays simulated (BattLeDIM L-Town); the dashboard's mandatory notices are unchanged.

## Verification on x86-64 (2026-10-18)

`tests/test_external_dashboard.py` (34 tests): gateway routes, authentication, refusal of
writes and of other routes without reaching the API, unreachable API, rate limit; snapshot
format, upload loop, skipped upload when the API is down, URL never taken from the command
line or printed with its signature; SigV4 against the AWS example; the dashboard's snapshot age
notice, stale warning, fallback and recovery, configuration precedence, and both pages headless.

Local end to end: the real API (`uvicorn sentinel.main:create_app --factory`, mocks, ingest
token) with a synthetic day replayed; the gateway in front of it answered 200 for the reads,
404 for `/docs`, `/openapi.json` and `/v1/audit/head`, 401 without the token and 405 for POST,
and its log had no token. The dashboard read it through the gateway, from a snapshot file
(notice "SNAPSHOT — copy of the API taken …", chip SNAPSHOT) and fell back to the synthetic
recording with the API down, with no browser errors and no horizontal overflow at 1280 × 720.
