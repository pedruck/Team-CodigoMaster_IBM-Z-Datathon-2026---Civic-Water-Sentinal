"""Read-only, password-protected gateway in front of the API, for a dashboard hosted off the VM
(option 1 of the external-dashboard plan, BE-026). Stdlib only and Python >= 3.8, because it
runs on the VM host, whose Python may be older than the container's.

Only these requests are forwarded, and only as GET (or HEAD):

    /health                               /v1/status
    /v1/alerts?limit&offset&since&until&region
    /v1/alerts/{alert_id}                 /v1/alerts/{alert_id}/explanation

Everything else gets 404 without touching the API: no POST, no /docs, no /openapi.json, no
audit read. Every request needs ``Authorization: Bearer <token>``; the token is read from a file
the gateway refuses unless only its owner can read it, and it never appears on a command line or
in a log. The gateway listens on 127.0.0.1 only: a tunnel (``ssh -R``, cloudflared) connects to
it from the same host.

    python3 scripts/readonly_gateway.py --upstream http://172.17.0.2:8000 \\
        --token-file ~/shared/civicwater/secrets/dashboard-gateway.token --port 8099
"""

from __future__ import annotations

import argparse
import hmac
import http.server
import json
import os
import re
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import IO

ALERT_ID = r"[A-Za-z0-9._:-]{1,128}"
ROUTES = (
    re.compile(r"^/health$"),
    re.compile(r"^/v1/status$"),
    re.compile(r"^/v1/alerts$"),
    re.compile(rf"^/v1/alerts/{ALERT_ID}$"),
    re.compile(rf"^/v1/alerts/{ALERT_ID}/explanation$"),
)
QUERY_KEYS = {"limit", "offset", "since", "until", "region"}
MAX_QUERY_VALUE = 64
MAX_BODY = 8 * 1024 * 1024  # an alert page of 500 is well under 1 MB
MIN_TOKEN_LENGTH = 24


class GatewayError(Exception):
    """The gateway cannot start with this configuration."""


def read_token(path: Path) -> str:
    """The shared secret, from a file only its owner can read (mode 600 or stricter)."""
    try:
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GatewayError(f"cannot read the token file {path}: {exc}") from exc
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise GatewayError(f"{path} is readable by others: chmod 600 {path}")
    token = text.strip()
    if len(token) < MIN_TOKEN_LENGTH or any(c.isspace() for c in token):
        raise GatewayError(f"the token must be one word of at least {MIN_TOKEN_LENGTH} characters")
    return token


def allowed_target(raw_path: str) -> str | None:
    """The upstream path and query for an allowed request, or None. The query is rebuilt from
    the known keys only, so nothing else reaches the API."""
    parts = urllib.parse.urlsplit(raw_path)
    path = parts.path
    if not any(route.match(path) for route in ROUTES):
        return None
    if parts.query and path != "/v1/alerts":
        return None
    params: list[tuple[str, str]] = []
    for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=False):
        if key not in QUERY_KEYS or len(value) > MAX_QUERY_VALUE:
            return None
        params.append((key, value))
    return path + (f"?{urllib.parse.urlencode(params)}" if params else "")


class RateLimiter:
    """At most ``rate`` requests per second per client address, with a small burst."""

    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic):
        self.rate = rate
        self.burst = float(burst)
        self.clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.get(client, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            allowed = tokens >= 1.0
            self._buckets[client] = (tokens - 1.0 if allowed else tokens, now)
            return allowed


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is answered as an error: the gateway never follows the API elsewhere."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class Gateway:
    def __init__(self, upstream: str, token: str, *, timeout_s: float, limiter: RateLimiter):
        if not re.match(r"^https?://[^/?#]+$", upstream.rstrip("/")):
            raise GatewayError(
                "--upstream must be scheme://host[:port], e.g. http://172.17.0.2:8000"
            )
        self.upstream = upstream.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.limiter = limiter
        self.opener = urllib.request.build_opener(_NoRedirect())

    def authorised(self, header: str | None) -> bool:
        if not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer ") :].encode(), self.token.encode())

    def fetch(self, target: str) -> tuple[int, bytes]:
        request = urllib.request.Request(
            self.upstream + target, headers={"Accept": "application/json"}, method="GET"
        )
        try:
            with self.opener.open(request, timeout=self.timeout_s) as resp:
                body = resp.read(MAX_BODY + 1)
                status = int(resp.status)
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_BODY + 1)
            status = int(exc.code)
        except (urllib.error.URLError, OSError) as exc:
            return 502, _error("upstream_unavailable", f"the API did not answer: {exc}")
        if len(body) > MAX_BODY:
            return 502, _error("upstream_too_large", "the API answer is larger than allowed")
        if 300 <= status < 400:
            return 502, _error("upstream_redirect", "the API answered with a redirect")
        return status, body


def _error(code: str, message: str) -> bytes:
    return json.dumps({"error": {"code": code, "message": message}}).encode()


def make_handler(gateway: Gateway, log: Callable[[str], None]) -> type:
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "civicwater-readonly-gateway"
        sys_version = ""

        def _send(self, status: int, body: bytes, head: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 401:
                self.send_header("WWW-Authenticate", 'Bearer realm="civicwater"')
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _serve(self, head: bool) -> None:
            client = self.client_address[0]
            if not gateway.limiter.allow(client):
                return self._send(429, _error("rate_limited", "too many requests"), head)
            if not gateway.authorised(self.headers.get("Authorization")):
                return self._send(401, _error("unauthorized", "bearer token required"), head)
            target = allowed_target(self.path)
            if target is None:
                return self._send(404, _error("not_found", "not available here"), head)
            status, body = gateway.fetch(target)
            self._send(status, body, head)

        def do_GET(self) -> None:
            self._serve(head=False)

        def do_HEAD(self) -> None:
            self._serve(head=True)

        def _refuse(self) -> None:
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # http.server dispatches on these names; every write method is refused.
        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            # Method, path and status only: never headers, so the token cannot reach the log.
            path = urllib.parse.urlsplit(self.path).path
            status = args[1] if len(args) > 1 else ""
            log(f"{self.log_date_time_string()} {self.command} {path} {status}")

    return Handler


def serve(host: str, port: int, gateway: Gateway) -> http.server.ThreadingHTTPServer:
    handler = make_handler(gateway, lambda line: print(line, file=sys.stderr, flush=True))
    server = http.server.ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--upstream", required=True, help="API base URL, e.g. http://172.17.0.2:8000")
    ap.add_argument(
        "--token-file", required=True, type=Path, help="file with the shared token (mode 600)"
    )
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--rate", type=float, default=5.0, help="requests per second per client")
    args = ap.parse_args(argv)
    try:
        gateway = Gateway(
            args.upstream,
            read_token(args.token_file.expanduser()),
            timeout_s=args.timeout,
            limiter=RateLimiter(args.rate, burst=20),
        )
    except GatewayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    server = serve("127.0.0.1", args.port, gateway)
    print(
        f"read-only gateway on http://127.0.0.1:{args.port} -> {gateway.upstream} "
        f"(pid {os.getpid()})",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
