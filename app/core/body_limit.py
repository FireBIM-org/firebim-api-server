"""Request body size limits (pure ASGI middleware).

``multipart/form-data`` bodies may be up to ``max_upload_bytes`` plus a small allowance for the multipart framing;
every other body is capped at ``max_json_body_bytes``. A declared ``Content-Length`` over the limit is refused
before anything is read, and streamed (chunked) bodies are counted as they arrive, so a request without a
``Content-Length`` cannot get around the limit. Both answer 413 ``{"code": "PAYLOAD_TOO_LARGE"}``.
"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics

# Boundaries, part headers and the odd small form field around one uploaded file.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024


class PayloadTooLargeError(Exception):
    """Raised from ``receive`` once a streamed body passes the limit, to stop the app reading it."""


def _content_type(scope: Scope) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == b"content-type":
            return value.decode("latin-1").lower()
    return ""


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers", []):
        if key.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class BodySizeLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_json_body_bytes: int,
        max_upload_bytes: int,
        multipart_overhead_bytes: int = MULTIPART_OVERHEAD_BYTES,
    ) -> None:
        self.app = app
        self.max_json_body_bytes = max_json_body_bytes
        self.max_multipart_bytes = max_upload_bytes + multipart_overhead_bytes

    def limit_for(self, scope: Scope) -> int:
        if _content_type(scope).startswith("multipart/form-data"):
            return self.max_multipart_bytes
        return self.max_json_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.limit_for(scope)
        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await self._reject(scope, receive, send, limit)
            return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise PayloadTooLargeError(f"request body exceeds {limit} bytes")
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded:
                # Whatever the app makes of the aborted read (often a 400), the answer is our 413.
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise

        if exceeded and not response_started:
            await self._reject(scope, receive, send, limit)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        metrics.record_storage_rejected("payload_too_large")
        response = JSONResponse(
            {"detail": f"Request body exceeds the limit of {limit} bytes", "code": "PAYLOAD_TOO_LARGE"},
            status_code=413,
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)


__all__ = ["MULTIPART_OVERHEAD_BYTES", "BodySizeLimitMiddleware", "PayloadTooLargeError"]
