"""Make a pre-signed S3-compatible URL (AWS S3, Cloudflare R2, GCS with HMAC keys) for the
external-dashboard snapshot (BE-026). Run it on your own computer, never on the VM: it needs the
storage credentials, which stay there. Stdlib only (AWS Signature Version 4, query signing).

    export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...   # from the storage provider
    # write URL for the VM (expires in 2 h):
    python scripts/presign_url.py PUT --endpoint https://<account>.r2.cloudflarestorage.com \\
        --region auto --bucket civicwater --key snapshot.json --expires 7200
    # read URL for the dashboard secrets (at most 7 days):
    python scripts/presign_url.py GET ... --expires 604800

The URL is printed on stdout only. Treat it as a password until it expires.
"""

import argparse
import datetime as dt
import hashlib
import hmac
import os
import sys
import urllib.parse

MAX_EXPIRES = 7 * 24 * 3600  # SigV4 limit


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="-_.~")


def presign(
    method: str,
    *,
    host: str,
    path: str,
    region: str,
    access_key: str,
    secret_key: str,
    when: dt.datetime,
    expires: int,
    scheme: str = "https",
    service: str = "s3",
) -> str:
    """A SigV4 query-string signed URL that allows ``method`` on ``path`` until it expires."""
    if not 1 <= expires <= MAX_EXPIRES:
        raise ValueError(f"expires must be 1..{MAX_EXPIRES} seconds")
    amz_date = when.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    scope = f"{day}/{region}/{service}/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    query = "&".join(f"{_quote(k)}={_quote(v)}" for k, v in sorted(params.items()))
    canonical_path = urllib.parse.quote(path, safe="/-_.~")
    canonical = "\n".join(
        [method, canonical_path, query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"]
    )
    to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        ]
    )
    key = f"AWS4{secret_key}".encode()
    for part in (day, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{scheme}://{host}{canonical_path}?{query}&X-Amz-Signature={signature}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("method", choices=["PUT", "GET"])
    ap.add_argument("--endpoint", required=True, help="https://<account>.r2.cloudflarestorage.com")
    ap.add_argument("--region", default="auto", help="'auto' for R2, e.g. us-east-1 for S3")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--key", default="snapshot.json")
    ap.add_argument("--expires", type=int, default=7200, help="seconds (max 604800)")
    args = ap.parse_args(argv)
    access = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if not access or not secret:
        print("error: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY", file=sys.stderr)
        return 2
    endpoint = urllib.parse.urlsplit(args.endpoint)
    if endpoint.scheme != "https" or not endpoint.netloc:
        print("error: --endpoint must be https://host", file=sys.stderr)
        return 2
    print(
        presign(
            args.method,
            host=endpoint.netloc,
            path=f"/{args.bucket}/{args.key.lstrip('/')}",
            region=args.region,
            access_key=access,
            secret_key=secret,
            when=dt.datetime.now(dt.UTC),
            expires=args.expires,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
