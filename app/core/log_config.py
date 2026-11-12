"""Process-wide logging setup for the API and the Celery worker.

``LOG_FORMAT=json`` (the default in staging/production) writes one JSON object per line to stdout with ``timestamp``,
``level``, ``logger``, ``message``, ``trace_id``, ``span_id``, ``service``, the request id when there is one, and any
``extra=`` fields (``log_event()`` fields are lifted to top-level keys). ``text`` keeps the plain format used locally.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO, Any

from opentelemetry import trace

from app.core.config import settings
from app.core.observability import LOG_FIELDS_ATTR, get_request_id

# Keys every JSON line has; extra fields never overwrite them.
CORE_FIELDS = ("timestamp", "level", "logger", "message", "trace_id", "span_id", "service")

# Attributes of a plain LogRecord, plus the ones added by Python, uvicorn and log_event().
_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", logging.INFO, "", 0, "", (), None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn: the message again, with ANSI colours
    LOG_FIELDS_ATTR,
}

# Uvicorn configures these before the app is imported; in JSON mode they propagate to the root handler instead.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _trace_ids() -> tuple[str | None, str | None]:
    """Trace/span id of the span active while logging (formatting runs synchronously in the logging call)."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


class JsonLogFormatter(logging.Formatter):
    """One JSON object per record, on a single line."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        trace_id, span_id = _trace_ids()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": trace_id,
            "span_id": span_id,
            "service": self.service,
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, http_version, status = record.args
            payload.update(client=client, method=method, path=path, http_version=http_version, status_code=status)
        for key, value in vars(record).items():
            if key not in _RECORD_ATTRS and not key.startswith("_"):
                payload.setdefault(key, value)
        fields = getattr(record, LOG_FIELDS_ATTR, None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                if value is not None:
                    payload.setdefault(str(key), value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def json_handler(service: str, stream: IO[str] | None = None) -> logging.Handler:
    # sys.__stdout__: the Celery worker redirects sys.stdout into logging, which must not loop back here.
    handler = logging.StreamHandler(stream or sys.__stdout__ or sys.stdout)
    handler.setFormatter(JsonLogFormatter(service))
    return handler


def configure_logging(
    service: str,
    *,
    log_format: str | None = None,
    level: int | str = logging.INFO,
    stream: IO[str] | None = None,
) -> str:
    """Set up the root logger for ``service``; returns the format used (``json`` or ``text``).

    Text mode is ``logging.basicConfig`` as before. JSON mode replaces the root handlers with a single JSON handler on
    stdout and routes uvicorn's own loggers through it."""
    log_format = log_format or settings.log_format
    root = logging.getLogger()
    if log_format != "json":
        logging.basicConfig(level=level)
        return "text"
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(json_handler(service, stream))
    root.setLevel(level)
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    return "json"


__all__ = ["CORE_FIELDS", "JsonLogFormatter", "configure_logging", "json_handler"]
