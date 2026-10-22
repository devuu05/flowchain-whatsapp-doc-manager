"""Request ID, body-size limit and security-header middleware (pure ASGI)."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

log = logging.getLogger("app.request")
_RID_RE = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")


class RequestContextMiddleware:
    """Assigns a request id, enforces a max body size and logs one line per request."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        rid = incoming if _RID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status_holder: dict[str, Any] = {"status": 500}

        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_body_bytes:
            await _send_json(send, 413, "payload_too_large", "Payload too large", rid)
            request_id_var.reset(token)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _BodyTooLarge
            return message

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                hdrs = list(message.get("headers", []))
                hdrs.append((b"x-request-id", rid.encode()))
                hdrs.append((b"x-content-type-options", b"nosniff"))
                hdrs.append((b"referrer-policy", b"no-referrer"))
                hdrs.append((b"x-frame-options", b"DENY"))
                hdrs.append((b"cache-control", b"no-store"))
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except _BodyTooLarge:
            await _send_json(send, 413, "payload_too_large", "Payload too large", rid)
            status_holder["status"] = 413
        finally:
            path = scope.get("path", "")
            # Signed media tokens live in the path; never log them.
            if path.startswith("/api/v1/media/"):
                path = "/api/v1/media/[token]"
            log.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": path,
                    "status": status_holder["status"],
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            request_id_var.reset(token)


class _BodyTooLarge(Exception):
    pass


async def _send_json(send: Send, status: int, code: str, message: str, rid: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message, "request_id": rid}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"x-request-id", rid.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
