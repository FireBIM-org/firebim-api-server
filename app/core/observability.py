from __future__ import annotations

import json
from contextvars import ContextVar
from time import monotonic
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
request_endpoint_var: ContextVar[str | None] = ContextVar("request_endpoint", default=None)
request_method_var: ContextVar[str | None] = ContextVar("request_method", default=None)
request_profiling_var: ContextVar[bool] = ContextVar("request_profiling", default=False)
request_metrics_var: ContextVar[dict[str, Any] | None] = ContextVar("request_metrics", default=None)
pending_db_checkout_var: ContextVar[tuple[str, float] | None] = ContextVar("pending_db_checkout", default=None)

# LogRecord attribute carrying log_event() fields; the JSON log formatter lifts them to top-level keys.
LOG_FIELDS_ATTR = "firebim_fields"


def set_request_context(*, request_id: str, endpoint: str, method: str, profiling_enabled: bool) -> dict[str, Any]:
    tokens = {
        "request_id": request_id_var.set(request_id),
        "endpoint": request_endpoint_var.set(endpoint),
        "method": request_method_var.set(method),
        "profiling": request_profiling_var.set(profiling_enabled),
        "metrics": request_metrics_var.set({}),
        "pending_checkout": pending_db_checkout_var.set(None),
    }
    return tokens


def reset_request_context(tokens: dict[str, Any]) -> None:
    pending_db_checkout_var.reset(tokens["pending_checkout"])
    request_metrics_var.reset(tokens["metrics"])
    request_profiling_var.reset(tokens["profiling"])
    request_method_var.reset(tokens["method"])
    request_endpoint_var.reset(tokens["endpoint"])
    request_id_var.reset(tokens["request_id"])


def get_request_id() -> str | None:
    return request_id_var.get()


def get_request_endpoint() -> str | None:
    return request_endpoint_var.get()


def get_request_method() -> str | None:
    return request_method_var.get()


def is_request_profiling_enabled() -> bool:
    return request_profiling_var.get()


def record_metric(name: str, value: Any, *, append: bool = False, accumulate: bool = False) -> None:
    metrics = request_metrics_var.get()
    if metrics is None:
        return
    if append:
        metrics.setdefault(name, []).append(value)
        return
    if accumulate:
        current = metrics.get(name, 0.0)
        metrics[name] = current + value
        return
    metrics[name] = value


def snapshot_metrics() -> dict[str, Any]:
    metrics = request_metrics_var.get()
    if not metrics:
        return {}
    return dict(metrics)


def start_db_checkout_wait(label: str) -> None:
    pending_db_checkout_var.set((label, monotonic()))


def finish_db_checkout_wait() -> tuple[str, float] | None:
    pending = pending_db_checkout_var.get()
    pending_db_checkout_var.set(None)
    if pending is None:
        return None
    label, started_at = pending
    return label, round((monotonic() - started_at) * 1000, 3)


def clear_db_checkout_wait() -> None:
    pending_db_checkout_var.set(None)


def log_event(logger, event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        "request_id": get_request_id(),
        "endpoint": get_request_endpoint(),
        "method": get_request_method(),
        **fields,
    }
    logger.info(json.dumps(payload, ensure_ascii=True, default=str, sort_keys=True), extra={LOG_FIELDS_ATTR: payload})
