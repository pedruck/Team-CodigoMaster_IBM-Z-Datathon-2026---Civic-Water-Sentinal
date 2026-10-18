#!/usr/bin/env bash
# Checks for a dashboard hosted off the VM (BE-026, docs/external-dashboard.md). Run on the VM
# HOST (ssh linuxone), not in the Jupyter terminal, after announcing it to the team.
#
#   scripts/external_dashboard_checks.sh prereq          # step 0: architecture, API, outbound
#   scripts/external_dashboard_checks.sh tunnel-test     # 1a: ssh -R to localhost.run, test page
#   scripts/external_dashboard_checks.sh cloudflared     # 1b: is there an official s390x binary?
#   scripts/external_dashboard_checks.sh snapshot-read   # 3a: read status and alerts as JSON
#   scripts/external_dashboard_checks.sh snapshot-put    # 3b: one PUT to a pre-signed URL
#   scripts/external_dashboard_checks.sh snapshot-loop   # 3c: 15 PUTs, one a minute
#
# Nothing here stores a secret: the pre-signed URL is typed without echo and kept in memory.
# tunnel-test only serves a page that says "ok", never the API.
set -euo pipefail

API="${API:-http://172.17.0.2:8000}"
TEST_PORT="${TEST_PORT:-8099}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

code() { curl -s -o /dev/null -m 10 -w '%{http_code}' "$@" || true; }

prereq() {
  echo "architecture: $(uname -m)   (expected s390x)"
  echo "python3: $(python3 --version 2>&1)   (the gateway and the snapshot script need >= 3.8)"
  echo "API $API/health: $(code "$API/health")   (expected 200)"
  for h in https://github.com https://localhost.run https://www.cloudflare.com \
           https://storage.googleapis.com https://s3.amazonaws.com; do
    echo "outbound $h: $(code "$h")   (000 = blocked)"
  done
  local rc=0
  ssh -o ConnectTimeout=8 -o BatchMode=yes -T nokey@localhost.run exit >/dev/null 2>&1 || rc=$?
  echo "outbound ssh (port 22): exit $rc   (255 = blocked)"
}

tunnel_test() {
  mkdir -p "$WORK/www" && echo ok > "$WORK/www/index.html"
  python3 -m http.server "$TEST_PORT" --bind 127.0.0.1 --directory "$WORK/www" >/dev/null 2>&1 &
  local server=$!
  trap 'kill $server 2>/dev/null; rm -rf "$WORK"' EXIT
  echo "test page on 127.0.0.1:$TEST_PORT (says 'ok'). Opening the tunnel; Ctrl-C closes both."
  echo "Pass: the printed https URL shows 'ok' on a phone OFF the Wi-Fi, for 30 minutes."
  ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
      -R "80:127.0.0.1:$TEST_PORT" nokey@localhost.run
}

cloudflared_check() {
  local url=https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-s390x
  echo "official s390x binary: $(code -IL "$url")   (404 = none; it can be built with Go off the VM)"
}

snapshot_read() {
  curl -sf -m 10 "$API/v1/status" > "$WORK/status.json"
  curl -sf -m 10 "$API/v1/alerts?limit=50" > "$WORK/alerts.json"
  python3 -c "import json,sys;[json.load(open(f)) for f in sys.argv[1:]];print('JSON ok')" \
    "$WORK/status.json" "$WORK/alerts.json"
}

read_url() {
  if [[ -z "${SNAPSHOT_PUT_URL:-}" ]]; then
    read -rsp "Pre-signed PUT URL (not shown, not saved): " SNAPSHOT_PUT_URL; echo
  fi
  export SNAPSHOT_PUT_URL
}

snapshot_put() {
  read_url
  python3 "$HERE/push_snapshot.py" --api "$API" --count 1
  echo "Pass: 200 here, and the object is readable from your own computer."
}

snapshot_loop() {
  read_url
  python3 "$HERE/push_snapshot.py" --api "$API" --every 60 --count "${COUNT:-15}"
  echo "Pass: one 200 line per minute, and taken_at in the stored object keeps changing."
}

case "${1:-}" in
  prereq) prereq ;;
  tunnel-test) tunnel_test ;;
  cloudflared) cloudflared_check ;;
  snapshot-read) snapshot_read ;;
  snapshot-put) snapshot_put ;;
  snapshot-loop) snapshot_loop ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
