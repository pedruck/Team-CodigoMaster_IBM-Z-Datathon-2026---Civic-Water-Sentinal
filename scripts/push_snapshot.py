"""Push a read-only snapshot of the API to external storage, for a dashboard hosted off the VM
(option 3 of the external-dashboard plan, BE-026). Stdlib only and Python >= 3.8 (VM host).

Every ``--every`` seconds it reads ``/health``, ``/v1/status`` and ``/v1/alerts`` and uploads
one JSON document with ``PUT`` to a pre-signed URL (S3, R2 or GCS). Nothing listens and no
storage credential reaches the VM: the URL is generated elsewhere (``scripts/presign_url.py``),
allows writing one object and expires. It is read from ``SNAPSHOT_PUT_URL`` or typed without
echo when that variable is absent; never pass it as an argument (it would land in the shell
history and the process list) and never save it in a file on the VM.

If the API does not answer, nothing is uploaded: the stored snapshot keeps its ``taken_at``, and
the dashboard shows how old it is.

    read -rs SNAPSHOT_PUT_URL && export SNAPSHOT_PUT_URL
    python3 scripts/push_snapshot.py --api http://172.17.0.2:8000 --every 60 --count 15
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

SCHEMA = "civicwater.dashboard-snapshot/1"
MAX_ALERTS = 500


class SnapshotError(Exception):
    """The API could not be read; nothing is uploaded."""


def _get_json(url: str, timeout_s: float) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SnapshotError(f"{url}: {exc}") from exc


def take_snapshot(
    api: str,
    *,
    limit: int = MAX_ALERTS,
    timeout_s: float = 10.0,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),  # noqa: UP017 (Python 3.8 on the VM host)
) -> dict[str, Any]:
    """One document with the API's own answers, unchanged; the dashboard validates them."""
    base = api.rstrip("/")
    health = _get_json(f"{base}/health", timeout_s)
    status = _get_json(f"{base}/v1/status", timeout_s)
    query = urllib.parse.urlencode({"limit": min(max(limit, 1), MAX_ALERTS)})
    alerts = _get_json(f"{base}/v1/alerts?{query}", timeout_s)
    return {
        "schema": SCHEMA,
        "taken_at": now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "health": health,
        "status": status,
        "alerts": alerts,
    }


def upload(url: str, document: dict[str, Any], timeout_s: float = 20.0) -> int:
    """PUT the document; returns the HTTP status (0 when the storage did not answer)."""
    body = json.dumps(document, separators=(",", ":"), allow_nan=False).encode()
    request = urllib.request.Request(
        url, data=body, method="PUT", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError):
        return 0


def put_url(
    env: dict[str, str] | None = None, prompt: Callable[[str], str] = getpass.getpass
) -> str:
    """The pre-signed URL: from ``SNAPSHOT_PUT_URL``, else typed without echo."""
    source = os.environ if env is None else env
    url = source.get("SNAPSHOT_PUT_URL", "").strip() or prompt("Pre-signed PUT URL: ").strip()
    if not url.startswith("https://"):
        raise SystemExit("error: the pre-signed URL must start with https://")
    return url


def redact(url: str) -> str:
    """The URL without its query, which carries the signature."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def run(
    api: str,
    url: str,
    *,
    every_s: float,
    count: int,
    limit: int,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> int:
    """Push ``count`` snapshots (0 = until interrupted). Returns the number of failed rounds."""
    failures = 0
    done = 0
    while count == 0 or done < count:
        stamp = time.strftime("%H:%M:%S")
        try:
            status = upload(url, take_snapshot(api, limit=limit))
        except SnapshotError as exc:
            failures += 1
            log(f"{stamp} skipped: {exc}")
        else:
            ok = 200 <= status < 300
            failures += 0 if ok else 1
            log(f"{stamp} {status if status else 'no answer'} {redact(url)}")
        done += 1
        if count == 0 or done < count:
            sleep(every_s)
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", required=True, help="API base URL, e.g. http://172.17.0.2:8000")
    ap.add_argument("--every", type=float, default=60.0, help="seconds between snapshots")
    ap.add_argument("--count", type=int, default=1, help="snapshots to push; 0 = until Ctrl-C")
    ap.add_argument("--limit", type=int, default=MAX_ALERTS, help="newest alerts to include")
    args = ap.parse_args(argv)
    url = put_url()
    try:
        failures = run(args.api, url, every_s=args.every, count=args.count, limit=args.limit)
    except KeyboardInterrupt:
        return 0
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
