"""Static security response headers for the API (pure ASGI middleware)."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
)


class SecurityHeadersMiddleware:
    """Adds ``SECURITY_HEADERS`` (unless a handler set them) and ``Cache-Control: no-store`` on authenticated JSON."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        authenticated = any(key.lower() == b"authorization" for key, _ in scope.get("headers", []))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    headers.setdefault(name, value)
                content_type = headers.get("content-type", "")
                if authenticated and content_type.startswith("application/json") and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = ["SECURITY_HEADERS", "SecurityHeadersMiddleware"]
