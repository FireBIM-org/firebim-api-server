"""API error responses with a machine-readable ``code`` and a generic handler for unexpected exceptions."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.security_headers import SECURITY_HEADERS

logger = logging.getLogger(__name__)


class ApiError(HTTPException):
    """An HTTPException rendered as ``{"detail": ..., "code": ...}``."""

    def __init__(self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def payload_too_large(detail: str) -> ApiError:
    return ApiError(413, detail, "PAYLOAD_TOO_LARGE")


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code, headers=exc.headers)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or str(uuid.uuid4())


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort 500: log the exception with the request id, never echo exception text to the client."""
    request_id = _request_id(request)
    logger.error(
        "Unhandled exception for %s %s (request_id=%s)",
        request.method,
        request.url.path,
        request_id,
        exc_info=exc,
        extra={"request_id": request_id},
    )
    # This response is produced outside the user middleware stack, so add the security headers here.
    headers = {name: value for name, value in SECURITY_HEADERS}
    headers["X-Request-ID"] = request_id
    return JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


__all__ = ["ApiError", "install_error_handlers", "payload_too_large", "unhandled_exception_handler"]
