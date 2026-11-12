from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from celery.signals import task_failure, task_postrun, task_prerun
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlmodel import Session

from app.core import metrics, telemetry
from app.core.config import settings
from app.core.log_config import CORE_FIELDS, JsonLogFormatter, configure_logging
from app.core.observability import LOG_FIELDS_ATTR, log_event
from app.models.models import UserActivity
from app.storage import quota

REPO_ROOT = Path(__file__).resolve().parents[1]


def _points(reader: InMemoryMetricReader) -> dict[str, list]:
    """Metric name -> data points of one collection."""
    points: dict[str, list] = {}
    data = reader.get_metrics_data()
    for resource_metrics in data.resource_metrics if data else []:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                points.setdefault(metric.name, []).extend(metric.data.data_points)
    return points


def _value(points: list, **attributes) -> float | None:
    for point in points:
        if dict(point.attributes) == attributes:
            return point.value
    return None


@pytest.fixture()
def tracing():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider, exporter
    provider.shutdown()


@pytest.fixture()
def metric_reader():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    yield provider, reader
    provider.shutdown()


@pytest.fixture()
def bound_counters(metric_reader):
    provider, reader = metric_reader
    metrics.bind_meter_provider(provider)
    yield reader
    metrics.bind_meter_provider(None)


# --- disabled path ---------------------------------------------------------------------------------------------------


def test_telemetry_is_a_noop_without_an_otlp_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)

    assert telemetry.telemetry_enabled() is False
    assert telemetry.init_telemetry(telemetry.API_SERVICE_NAME) is False
    # Nothing global was installed and the app was not instrumented.
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)
    from app.main import app

    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)
    # The hooks stay safe to call.
    metrics.record_rate_limited("anon")
    metrics.record_storage_rejected("storage_limit")
    metrics.record_storage_used(used_bytes=1, object_count=1, limit_bytes=10)
    metrics.record_celery_task("process_model", "success", 1.5)


def test_otel_sdk_disabled_wins_over_an_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy.monitoring.svc.cluster.local:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    assert telemetry.telemetry_enabled() is False
    assert telemetry.init_telemetry(telemetry.WORKER_SERVICE_NAME) is False
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)


def test_resource_uses_defaults_unless_the_environment_sets_them(monkeypatch):
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    attributes = telemetry.build_resource("firebim-api").attributes
    assert attributes["service.name"] == "firebim-api"
    assert attributes["service.version"] == "abc1234"
    assert attributes["deployment.environment"] == settings.ENVIRONMENT

    monkeypatch.setenv("OTEL_SERVICE_NAME", "firebim-api-canary")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=staging,k8s.cluster.name=firebim-staging")
    monkeypatch.setenv("GIT_COMMIT", "unknown")
    attributes = telemetry.build_resource(telemetry.service_name_for("firebim-api")).attributes
    assert attributes["service.name"] == "firebim-api-canary"
    assert attributes["deployment.environment"] == "staging"
    assert attributes["k8s.cluster.name"] == "firebim-staging"
    assert "service.version" not in attributes


def test_excluded_urls_cover_probes_and_accept_more_from_the_environment(monkeypatch):
    monkeypatch.setenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "/build-info$")
    assert telemetry.excluded_urls().split(",") == [*telemetry.EXCLUDED_URLS, "/build-info$"]


# --- traces ----------------------------------------------------------------------------------------------------------


def test_request_produces_a_server_span_with_the_request_id(tracing, metric_reader):
    from app.main import request_observability_middleware

    tracer_provider, exporter = tracing
    meter_provider, reader = metric_reader
    app = FastAPI()
    app.middleware("http")(request_observability_middleware)

    @app.get("/items/{item_id}")
    def get_item(item_id: str):
        return {"id": item_id}

    @app.get("/livez")
    def livez():
        return {"status": "ok"}

    telemetry.instrument_fastapi_app(app, tracer_provider=tracer_provider, meter_provider=meter_provider)
    telemetry.instrument_fastapi_app(app, tracer_provider=tracer_provider, meter_provider=meter_provider)  # idempotent

    with TestClient(app) as client:
        response = client.get("/items/42", headers={"X-Request-ID": "req-123"})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "req-123"
        assert client.get("/livez").status_code == 200
        generated = client.get("/items/7").headers["X-Request-ID"]

    server_spans = [span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER]
    assert [span.name for span in server_spans] == ["GET /items/{item_id}", "GET /items/{item_id}"]  # /livez excluded
    assert server_spans[0].attributes[telemetry.REQUEST_ID_ATTRIBUTE] == "req-123"
    assert server_spans[1].attributes[telemetry.REQUEST_ID_ATTRIBUTE] == generated
    # No per-message ASGI send/receive spans.
    assert all(span.kind == SpanKind.SERVER for span in exporter.get_finished_spans())
    # HTTP server metrics are recorded too.
    assert any(name.startswith("http.server") for name in _points(reader))


def test_request_id_attribute_is_skipped_without_a_recording_span():
    telemetry.set_request_id_attribute("req-1")  # no active span: nothing happens, nothing raises


# --- counters --------------------------------------------------------------------------------------------------------


def test_rejection_counters_report(bound_counters, monkeypatch):
    metrics.record_rate_limited("anon")
    metrics.record_rate_limited("anon")
    metrics.record_rate_limited("expensive")
    metrics.record_storage_rejected("payload_too_large")

    # A write over the storage cap goes through the real accounting path.
    monkeypatch.setattr(settings, "STORAGE_HARD_LIMIT_BYTES", 10)
    with pytest.raises(quota.StorageLimitExceeded):
        quota.reserve(11, prefix="models")

    points = _points(bound_counters)
    assert _value(points["firebim.ratelimit.rejected"], route_class="anon") == 2
    assert _value(points["firebim.ratelimit.rejected"], route_class="expensive") == 1
    assert _value(points["firebim.storage.rejected"], reason="payload_too_large") == 1
    assert _value(points["firebim.storage.rejected"], reason="storage_limit") == 1


class _FakeTask:
    """Signal senders must be hashable (SimpleNamespace is not)."""

    def __init__(self, name: str) -> None:
        self.name = name


def _task(name: str = "process_model") -> _FakeTask:
    return _FakeTask(name)


def test_celery_signals_record_task_counts_and_durations(bound_counters):
    from app.workers import observability  # noqa: F401  (connects the signal handlers)

    task = _task()
    task_prerun.send(sender=task, task_id="t-1", task=task, args=(), kwargs={})
    task_postrun.send(sender=task, task_id="t-1", task=task, args=(), kwargs={}, retval=None, state="SUCCESS")

    task_prerun.send(sender=task, task_id="t-2", task=task, args=(), kwargs={})
    task_failure.send(sender=task, task_id="t-2", exception=RuntimeError("boom"), args=(), kwargs={})
    task_postrun.send(sender=task, task_id="t-2", task=task, args=(), kwargs={}, retval=None, state=None)

    points = _points(bound_counters)
    assert _value(points["firebim.celery.tasks"], task="process_model", state="success") == 1
    assert _value(points["firebim.celery.tasks"], task="process_model", state="failure") == 1
    durations = {point.attributes["state"]: point for point in points["firebim.celery.task.duration"]}
    assert durations["success"].count == 1
    assert durations["failure"].count == 1
    assert durations["success"].sum >= 0
    assert not observability._started and not observability._failed


# --- gauges ----------------------------------------------------------------------------------------------------------


def test_storage_and_user_gauges_read_the_accounting_tables(session: Session, test_user, metric_reader, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_HARD_LIMIT_BYTES", 1_000_000)
    quota.reserve(1234, prefix="models", user_id=test_user.id)
    quota.reserve(66, prefix="graphs")
    session.add(UserActivity(user_id=test_user.id, last_seen_at=datetime.now(UTC) - timedelta(days=1)))
    session.add(UserActivity(user_id="someone-else", last_seen_at=datetime.now(UTC) - timedelta(days=30)))
    session.commit()

    provider, reader = metric_reader
    metrics.register_usage_gauges(provider)
    points = _points(reader)

    assert _value(points["firebim.storage.used"]) == 1300
    assert _value(points["firebim.storage.objects"]) == 2
    assert _value(points["firebim.storage.limit"]) == 1_000_000
    assert _value(points["firebim.storage.prefix.used"], prefix="models") == 1234
    assert _value(points["firebim.storage.prefix.used"], prefix="graphs") == 66
    assert _value(points["firebim.users"], state="total") == 1
    assert _value(points["firebim.users"], state="active_7d") == 1


def test_gauges_query_the_database_at_most_once_per_ttl(metric_reader):
    provider, reader = metric_reader
    calls = []

    def loader():
        calls.append(1)
        return metrics.UsageSnapshot(used_bytes=5, object_count=1, limit_bytes=10, by_prefix={"ids": 5})

    metrics.register_usage_gauges(provider, loader=loader, ttl_seconds=60)
    for _ in range(3):
        assert _value(_points(reader)["firebim.storage.used"]) == 5
    assert len(calls) == 1  # five gauges, three collections, one query


def test_gauge_cache_keeps_the_last_snapshot_when_the_database_fails():
    results = [metrics.UsageSnapshot(used_bytes=5, object_count=1, limit_bytes=10)]

    def loader():
        if not results:
            raise RuntimeError("database down")
        return results.pop()

    cache = metrics.UsageSnapshotCache(loader, ttl_seconds=0)
    assert cache.get().used_bytes == 5
    assert cache.get().used_bytes == 5  # the reload failed: last good values
    cache.note_totals(used_bytes=7, object_count=2, limit_bytes=10)
    assert cache.get().used_bytes == 7

    empty = metrics.UsageSnapshotCache(lambda: (_ for _ in ()).throw(RuntimeError("down")), ttl_seconds=0)
    assert empty.get() is None


def test_gauges_report_nothing_while_the_database_is_unavailable(metric_reader):
    provider, reader = metric_reader

    def loader():
        raise RuntimeError("database down")

    metrics.register_usage_gauges(provider, loader=loader)
    points = _points(reader)
    assert "firebim.storage.used" not in points


# --- logs ------------------------------------------------------------------------------------------------------------


def _record(message: str = "hello %s", *args, name: str = "app.test", **extra) -> logging.LogRecord:
    logger = logging.getLogger(name)
    return logger.makeRecord(name, logging.INFO, __file__, 1, message, args or ("world",), None, extra=extra or None)


def test_json_log_line_has_core_fields_trace_ids_and_extras(tracing):
    tracer_provider, _ = tracing
    formatter = JsonLogFormatter("firebim-api")
    with tracer_provider.get_tracer("test").start_as_current_span("work") as span:
        line = formatter.format(_record(origins=["https://app.firebim.org"]))
        context = span.get_span_context()

    assert "\n" not in line
    entry = json.loads(line)
    assert set(CORE_FIELDS) <= set(entry)
    assert entry["message"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.test"
    assert entry["service"] == "firebim-api"
    assert entry["trace_id"] == format(context.trace_id, "032x")
    assert entry["span_id"] == format(context.span_id, "016x")
    assert entry["origins"] == ["https://app.firebim.org"]


def test_json_log_line_without_a_span_and_with_an_exception():
    formatter = JsonLogFormatter("firebim-worker")
    try:
        raise ValueError("bad\ninput")
    except ValueError:
        record = logging.getLogger("x").makeRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    line = formatter.format(record)
    assert "\n" not in line
    entry = json.loads(line)
    assert entry["trace_id"] is None and entry["span_id"] is None
    assert "ValueError: bad" in entry["exception"]


def test_log_event_keeps_its_text_message_and_lifts_fields_in_json(caplog):
    logger = logging.getLogger("app.test.events")
    with caplog.at_level(logging.INFO, logger="app.test.events"):
        log_event(logger, "db.session.close", duration_ms=1.5, message="not a core field")
    record = caplog.records[-1]
    payload = json.loads(record.getMessage())  # text output unchanged: the JSON payload as the message
    assert payload["event"] == "db.session.close"
    assert getattr(record, LOG_FIELDS_ATTR)["duration_ms"] == 1.5

    entry = json.loads(JsonLogFormatter("firebim-api").format(record))
    assert entry["event"] == "db.session.close"
    assert entry["duration_ms"] == 1.5
    assert entry["message"] == record.getMessage()  # fields never overwrite core keys


def test_configure_logging_json_mode_writes_one_json_line_per_record():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    access = logging.getLogger("uvicorn.access")
    saved_access = (list(access.handlers), access.propagate)
    stream = io.StringIO()
    try:
        assert configure_logging("firebim-api", log_format="json", stream=stream) == "json"
        logging.getLogger("app.test.json").warning("first")
        access.info('%s - "%s %s HTTP/%s" %d', "10.42.0.1:5000", "GET", "/livez", "1.1", 200)
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        access.handlers[:], access.propagate = saved_access
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    first, access_line = (json.loads(line) for line in lines)
    assert first["message"] == "first" and first["level"] == "WARNING"
    assert access_line["path"] == "/livez" and access_line["status_code"] == 200


def test_log_format_defaults_to_json_in_deployed_environments():
    assert settings.model_copy(update={"ENVIRONMENT": "production", "LOG_FORMAT": None}).log_format == "json"
    assert settings.model_copy(update={"ENVIRONMENT": "staging", "LOG_FORMAT": None}).log_format == "json"
    assert settings.model_copy(update={"ENVIRONMENT": "local", "LOG_FORMAT": None}).log_format == "text"
    assert settings.model_copy(update={"ENVIRONMENT": "production", "LOG_FORMAT": "text"}).log_format == "text"


# --- enabled path, end to end in a fresh process ---------------------------------------------------------------------

_ENABLED_SCRIPT = textwrap.dedent(
    """
    import json
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from app.core import telemetry

    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    build = telemetry.build_providers
    telemetry.build_providers = lambda name, **kw: build(
        name, span_processor=SimpleSpanProcessor(exporter), metric_readers=[reader]
    )

    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from app.main import app

    assert telemetry.init_telemetry(telemetry.API_SERVICE_NAME) is True  # second call: no-op
    client = TestClient(app)
    client.get("/build-info", headers={"X-Request-ID": "req-e2e"})
    client.get("/livez")

    # The API queues tasks by name (send_task); the trace context must travel in the message headers.
    from celery.signals import after_task_publish
    from opentelemetry import trace
    from app.workers.celery_app import celery_app
    published = {}
    after_task_publish.connect(lambda headers=None, **kw: published.update(headers or {}), weak=False)
    with trace.get_tracer("e2e").start_as_current_span("queue") as span:
        celery_app.send_task("process_model", args=("model-id", "models/x.ifc"))
        queue_trace_id = format(span.get_span_context().trace_id, "032x")
    still_current = trace.get_current_span().is_recording()

    names = sorted({m.name for rm in reader.get_metrics_data().resource_metrics
                    for sm in rm.scope_metrics for m in sm.metrics})
    server = [s for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    print(json.dumps({
        "spans": [s.name for s in server],
        "request_id": server[0].attributes.get("firebim.request_id") if server else None,
        "service": server[0].resource.attributes.get("service.name") if server else None,
        "instrumented": [
            BotocoreInstrumentor().is_instrumented_by_opentelemetry,
            CeleryInstrumentor().is_instrumented_by_opentelemetry,
            HTTPXClientInstrumentor().is_instrumented_by_opentelemetry,
            RequestsInstrumentor().is_instrumented_by_opentelemetry,
        ],
        "metrics": names,
        "traceparent_matches": (published.get("traceparent") or "").split("-")[1:2] == [queue_trace_id],
        "context_leaked": still_current,
    }))
    """
)


def test_enabled_telemetry_instruments_the_api_end_to_end():
    env = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    env.update(
        OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:9",
        OTEL_SERVICE_NAME="firebim-api",
        CELERY_BROKER_URL="memory://",
        CELERY_RESULT_BACKEND="cache+memory://",
    )
    result = subprocess.run(
        [sys.executable, "-c", _ENABLED_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["spans"] == ["GET /build-info"]  # /livez is not traced
    assert report["request_id"] == "req-e2e"
    assert report["service"] == "firebim-api"
    assert report["instrumented"] == [True, True, True, True]
    assert "http.server.request.duration" in report["metrics"]
    assert report["traceparent_matches"] is True
    assert report["context_leaked"] is False  # stable semconv -> http_server_request_duration_seconds
