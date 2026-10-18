"""Write guard for the HTTP API: bearer token and body size on every ``POST``.

Reads stay open (dashboard, evaluation, audit readers; ADR-009). Writes need
``Authorization: Bearer <SENTINEL_INGEST_TOKEN>`` when a token is configured, so a test or a
notebook in the shared container cannot feed the integration API by accident. Checked before
the body is read, so an unauthenticated client never reaches validation. Bodies are capped at
``SENTINEL_MAX_BODY_BYTES`` (a window is about 1 kB).

Not a defence against someone who can read the token file: the VM has one Linux user.
"""

import hmac
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


async def _reply(
    send: Send,
    status: int,
    code: str,
    message: str,
    extra_headers: tuple[tuple[bytes, bytes], ...] = (),
) -> None:
    body = json.dumps({"error": {"code": code, "message": message, "details": None}}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *extra_headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class WriteGuard:
    def __init__(self, app: ASGIApp, *, token: str | None, max_body_bytes: int) -> None:
        self.app = app
        self._token = token.encode() if token is not None else None
        self._max = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        if self._token is not None and not self._authorized(headers.get(b"authorization", b"")):
            await _reply(
                send,
                401,
                "unauthorized",
                "missing or wrong bearer token (SENTINEL_INGEST_TOKEN)",
                ((b"www-authenticate", b"Bearer"),),
            )
            return
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max:
            await self._too_large(send)
            return
        # Bodies are small: read it whole (bounded), then hand it to the app unchanged.
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self._max:
                await self._too_large(send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    def _authorized(self, header: bytes) -> bool:
        scheme, _, value = header.partition(b" ")
        assert self._token is not None
        return scheme.lower() == b"bearer" and hmac.compare_digest(value.strip(), self._token)

    async def _too_large(self, send: Send) -> None:
        await _reply(
            send,
            413,
            "payload_too_large",
            f"request body larger than {self._max} bytes (SENTINEL_MAX_BODY_BYTES)",
        )
