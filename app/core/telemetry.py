"""OpenTelemetry traces and metrics for the API (``firebim-api``) and the Celery worker (``firebim-worker``).

``init_telemetry()`` does nothing unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set (and ``OTEL_SDK_DISABLED`` is not
``true``), so tests and local runs stay quiet. When enabled it installs a TracerProvider (batched OTLP/HTTP span export)
and a MeterProvider (OTLP/HTTP metric export every 30 s) as the global providers, and instruments SQLAlchemy, botocore,
httpx, requests and Celery (producer spans in the API, task spans in the worker; the trace context travels in the task
headers). The standard ``OTEL_*`` variables (sampler, resource attributes, service name, exporter headers) apply.

The worker must call it in each pool process (``worker_process_init``), never before the fork: exporter threads do
not survive a fork. See app/workers/observability.py.
"""

from __future__ import annotations

import logging
import os
import threading

from fastapi import FastAPI
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, OTELResourceDetector, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanProcessor

from app.core.config import settings

logger = logging.getLogger(__name__)

API_SERVICE_NAME = "firebim-api"
WORKER_SERVICE_NAME = "firebim-worker"

METRIC_EXPORT_INTERVAL_MS = 30_000
# Probe and scrape paths are never traced (regexes matched against the URL without its query string).
EXCLUDED_URLS = ("/livez$", "/readyz$", "/health$", "/metrics$")
# Span attribute carrying the request's X-Request-ID (set by the request middleware in app/main.py).
REQUEST_ID_ATTRIBUTE = "firebim.request_id"

_lock = threading.Lock()
_initialized = False
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None


def telemetry_enabled() -> bool:
    """True when an OTLP endpoint is configured and the SDK is not disabled."""
    if os.getenv("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return False
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


def service_name_for(default: str) -> str:
    """``OTEL_SERVICE_NAME`` when set (the deployment sets it), else the built-in name of the process."""
    return os.getenv("OTEL_SERVICE_NAME", "").strip() or default


def build_resource(service_name: str) -> Resource:
    """service.name, deployment.environment and service.version (``GIT_COMMIT``) unless ``OTEL_SERVICE_NAME`` /
    ``OTEL_RESOURCE_ATTRIBUTES`` already set them; the environment always wins."""
    attributes: dict[str, str] = {SERVICE_NAME: service_name, "deployment.environment": settings.ENVIRONMENT}
    git_commit = os.getenv("GIT_COMMIT", "").strip()
    if git_commit and git_commit.lower() != "unknown":
        attributes[SERVICE_VERSION] = git_commit
    attributes.update({key: str(value) for key, value in OTELResourceDetector().detect().attributes.items()})
    return Resource.create(attributes)


def build_providers(
    service_name: str,
    *,
    span_processor: SpanProcessor | None = None,
    metric_readers: list[MetricReader] | None = None,
) -> tuple[TracerProvider, MeterProvider]:
    """Providers with OTLP/HTTP exporters (or the given processor/readers, e.g. in-memory ones in tests)."""
    resource = build_resource(service_name)
    if span_processor is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter: SpanExporter = OTLPSpanExporter()
        span_processor = BatchSpanProcessor(exporter)
    if metric_readers is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        metric_readers = [
            PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=METRIC_EXPORT_INTERVAL_MS)
        ]
    tracer_provider = TracerProvider(resource=resource)  # sampler from OTEL_TRACES_SAMPLER(_ARG)
    tracer_provider.add_span_processor(span_processor)
    meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
    return tracer_provider, meter_provider


def _instrument_libraries(tracer_provider: TracerProvider, meter_provider: MeterProvider) -> None:
    from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from app.models.database import engine

    providers = {"tracer_provider": tracer_provider, "meter_provider": meter_provider}
    SQLAlchemyInstrumentor().instrument(engine=engine, enable_commenter=False, **providers)
    BotocoreInstrumentor().instrument(**providers)
    HTTPXClientInstrumentor().instrument(**providers)
    RequestsInstrumentor().instrument(**providers)
    CeleryInstrumentor().instrument(**providers)
    # Adds otelTraceID/otelSpanID to every LogRecord; the log format itself stays ours (app/core/log_config.py).
    LoggingInstrumentor().instrument(tracer_provider=tracer_provider, set_logging_format=False)


def init_telemetry(service_name: str) -> bool:
    """Install the global tracer/meter providers and instrument the libraries, once per process.

    Returns True when telemetry is active (now or from an earlier call), False when it is disabled."""
    global _initialized, _tracer_provider, _meter_provider
    if not telemetry_enabled():
        return False
    with _lock:
        if _initialized:
            return True
        _initialized = True
        protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf").strip()
        if protocol != "http/protobuf":
            logger.warning("OTEL_EXPORTER_OTLP_PROTOCOL=%s is not supported; exporting with http/protobuf", protocol)
        # Stable HTTP semantic conventions: http.server.request.duration in seconds (http_server_request_duration_
        # seconds_* in Prometheus). Must be set before the first instrumentor reads it.
        os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
        tracer_provider, meter_provider = build_providers(service_name_for(service_name))
        trace.set_tracer_provider(tracer_provider)
        otel_metrics.set_meter_provider(meter_provider)
        _tracer_provider, _meter_provider = tracer_provider, meter_provider
        try:
            _instrument_libraries(tracer_provider, meter_provider)
        except Exception:
            logger.exception("OpenTelemetry instrumentation failed; continuing with partial telemetry")
    logger.info("OpenTelemetry enabled for %s", service_name_for(service_name))
    return True


def excluded_urls() -> str:
    """Our probe exclusions plus ``OTEL_PYTHON_FASTAPI_EXCLUDED_URLS`` / ``OTEL_PYTHON_EXCLUDED_URLS``."""
    extra = os.getenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS") or os.getenv("OTEL_PYTHON_EXCLUDED_URLS") or ""
    return ",".join([*EXCLUDED_URLS, *(url.strip() for url in extra.split(",") if url.strip())])


def instrument_fastapi_app(
    app: FastAPI,
    *,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
) -> None:
    """Server spans and HTTP server metrics for ``app``. Call before the app serves its first request (the middleware
    stack is built then). Idempotent per app."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    if getattr(app, "_is_instrumented_by_opentelemetry", False):
        return
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        excluded_urls=excluded_urls(),
        # The per-message ASGI send/receive spans only add noise.
        exclude_spans=["receive", "send"],
    )


def set_request_id_attribute(request_id: str) -> None:
    """Attach the request id to the active server span (no-op when nothing is recording)."""
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute(REQUEST_ID_ATTRIBUTE, request_id)


def shutdown_telemetry() -> None:
    """Flush and stop the exporters (worker pool processes exit without running atexit handlers)."""
    global _tracer_provider, _meter_provider
    with _lock:
        tracer_provider, meter_provider = _tracer_provider, _meter_provider
        _tracer_provider = _meter_provider = None
    for provider in (tracer_provider, meter_provider):
        if provider is None:
            continue
        try:
            provider.shutdown()
        except Exception:
            logger.debug("OpenTelemetry shutdown failed", exc_info=True)


__all__ = [
    "API_SERVICE_NAME",
    "EXCLUDED_URLS",
    "REQUEST_ID_ATTRIBUTE",
    "WORKER_SERVICE_NAME",
    "build_providers",
    "build_resource",
    "excluded_urls",
    "init_telemetry",
    "instrument_fastapi_app",
    "service_name_for",
    "set_request_id_attribute",
    "shutdown_telemetry",
    "telemetry_enabled",
]
