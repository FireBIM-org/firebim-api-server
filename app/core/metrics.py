"""Application metrics (OpenTelemetry instruments, exported by app/core/telemetry.py).

The hardening code calls the ``record_*`` hooks at the points worth counting. They are cheap, synchronous and never
raise; without a configured MeterProvider (tests, local) the instruments are the API's no-ops.

- ``firebim.ratelimit.rejected{route_class}`` and ``firebim.storage.rejected{reason}`` counters.
- ``firebim.celery.tasks{task,state}`` counter and ``firebim.celery.task.duration{task,state}`` histogram (seconds),
  fed by the worker's Celery signal handlers (app/workers/observability.py).
- Observable gauges registered by the API only (``register_usage_gauges``): ``firebim.storage.used``/``limit``/
  ``objects``, ``firebim.storage.prefix.used{prefix}`` and ``firebim.users{state=total|active_7d}``. Their callbacks
  read the accounting tables through a snapshot cached for ``USAGE_CACHE_SECONDS``, so every replica reports the
  current database values and an export never queries the database more than once a minute. The queries run with
  instrumentation suppressed: the metric reader's thread has no request span, so each would be a trace of its own.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

from opentelemetry import metrics as otel_metrics
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.metrics import CallbackOptions, Meter, MeterProvider, Observation

logger = logging.getLogger(__name__)

METER_NAME = "firebim"
USAGE_CACHE_SECONDS = 60.0
# Celery tasks run from milliseconds up to the 28 min hard time limit.
CELERY_DURATION_BUCKETS = (0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1200, 1800)


class _Instruments:
    def __init__(self, meter: Meter) -> None:
        self.ratelimit_rejected = meter.create_counter(
            "firebim.ratelimit.rejected", unit="{request}", description="Requests rejected with 429 by the rate limiter"
        )
        self.storage_rejected = meter.create_counter(
            "firebim.storage.rejected",
            unit="{request}",
            description="Writes refused by a size limit, the storage cap or unavailable accounting",
        )
        self.celery_tasks = meter.create_counter(
            "firebim.celery.tasks", unit="{task}", description="Celery tasks finished, by final state"
        )
        self.celery_task_duration = meter.create_histogram(
            "firebim.celery.task.duration",
            unit="s",
            description="Celery task run time",
            explicit_bucket_boundaries_advisory=list(CELERY_DURATION_BUCKETS),
        )


def _meter(provider: MeterProvider | None = None) -> Meter:
    # Without a provider: the global proxy meter, whose instruments start recording once telemetry sets the provider.
    return provider.get_meter(METER_NAME) if provider is not None else otel_metrics.get_meter(METER_NAME)


_instruments = _Instruments(_meter())


def bind_meter_provider(provider: MeterProvider | None) -> None:
    """Record the counters on ``provider`` (tests), or on the global provider again with ``None``."""
    global _instruments
    _instruments = _Instruments(_meter(provider))


def _safe(record: Callable[..., Any], *args: Any) -> None:
    try:
        record(*args)
    except Exception:  # metrics must never break a request or a task
        logger.debug("Recording a metric failed", exc_info=True)


def record_rate_limited(route_class: str) -> None:
    """A request was rejected with 429. ``route_class`` is ``default``, ``expensive`` or ``anon``."""
    _safe(_instruments.ratelimit_rejected.add, 1, {"route_class": route_class})


def record_storage_rejected(reason: str) -> None:
    """A write was refused by a storage or size limit (e.g. ``payload_too_large``, ``storage_limit``)."""
    _safe(_instruments.storage_rejected.add, 1, {"reason": reason})


def record_celery_task(task: str, state: str, duration_seconds: float | None) -> None:
    """A Celery task finished in ``state`` (``success``, ``failure``, ``retry``, ...), with its run time when known."""
    attributes = {"task": task, "state": state}
    _safe(_instruments.celery_tasks.add, 1, attributes)
    if duration_seconds is not None:
        _safe(_instruments.celery_task_duration.record, max(duration_seconds, 0.0), attributes)


# --- usage gauges (API) ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageSnapshot:
    used_bytes: int
    object_count: int
    limit_bytes: int
    by_prefix: dict[str, int] = field(default_factory=dict)
    users: dict[str, int] = field(default_factory=dict)


def load_usage_snapshot(engine=None) -> UsageSnapshot:
    """Read the storage counters and user counts from the database (raises on database errors)."""
    from sqlalchemy import func, select
    from sqlmodel import Session

    from app.models.models import User, UserActivity
    from app.storage import quota

    engine = engine or quota.accounting_engine()
    summary = quota.get_storage_summary(engine)
    cutoff = datetime.now(UTC) - timedelta(days=7)
    with Session(engine) as session:
        total = session.execute(select(func.count()).select_from(User.__table__)).scalar_one()
        active_7d = session.execute(
            select(func.count()).select_from(UserActivity.__table__).where(UserActivity.last_seen_at >= cutoff)
        ).scalar_one()
    return UsageSnapshot(
        used_bytes=summary.used_bytes,
        object_count=summary.object_count,
        limit_bytes=summary.limit_bytes,
        by_prefix={entry.prefix: entry.bytes for entry in summary.by_prefix},
        users={"total": int(total), "active_7d": int(active_7d)},
    )


class UsageSnapshotCache:
    """The last usage snapshot, reloaded at most every ``ttl_seconds`` (also after a failed load)."""

    def __init__(self, loader: Callable[[], UsageSnapshot], ttl_seconds: float = USAGE_CACHE_SECONDS) -> None:
        self._loader = loader
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._snapshot: UsageSnapshot | None = None
        self._loaded_at: float | None = None

    def get(self) -> UsageSnapshot | None:
        with self._lock:
            now = monotonic()
            if self._loaded_at is None or now - self._loaded_at >= self._ttl:
                self._loaded_at = now
                try:
                    with suppress_instrumentation():
                        self._snapshot = self._loader()
                except Exception as exc:
                    # Keep reporting the last good values; try again after the TTL.
                    logger.warning("Could not read storage usage for metrics: %s", exc)
            return self._snapshot

    def note_totals(self, *, used_bytes: int, object_count: int, limit_bytes: int) -> None:
        """Fresher bucket totals seen by this process (after a write or reconcile), kept until the next reload."""
        with self._lock:
            if self._snapshot is not None:
                self._snapshot = replace(
                    self._snapshot, used_bytes=used_bytes, object_count=object_count, limit_bytes=limit_bytes
                )


_usage_cache: UsageSnapshotCache | None = None


def record_storage_used(*, used_bytes: int, object_count: int, limit_bytes: int) -> None:
    """Bucket usage seen by this process after an accounting update or a reconcile. Refreshes the totals of the cached
    gauge snapshot; the database stays the gauge source."""
    cache = _usage_cache
    if cache is not None:
        _safe(lambda: cache.note_totals(used_bytes=used_bytes, object_count=object_count, limit_bytes=limit_bytes))


def _observe(cache: UsageSnapshotCache, read: Callable[[UsageSnapshot], Iterable[Observation]]):
    def callback(options: CallbackOptions) -> Iterable[Observation]:
        snapshot = cache.get()
        return list(read(snapshot)) if snapshot is not None else []

    return callback


def register_usage_gauges(
    provider: MeterProvider | None = None,
    *,
    loader: Callable[[], UsageSnapshot] | None = None,
    ttl_seconds: float = USAGE_CACHE_SECONDS,
) -> UsageSnapshotCache:
    """Register the storage and user gauges. The API calls this once with telemetry enabled (later calls return the
    same cache); tests pass their own ``provider``."""
    global _usage_cache
    if provider is None and _usage_cache is not None:
        return _usage_cache
    cache = UsageSnapshotCache(loader or load_usage_snapshot, ttl_seconds)
    meter = _meter(provider)
    meter.create_observable_gauge(
        "firebim.storage.used",
        callbacks=[_observe(cache, lambda s: [Observation(s.used_bytes)])],
        unit="By",
        description="Bytes stored in the bucket (accounting counters)",
    )
    meter.create_observable_gauge(
        "firebim.storage.limit",
        callbacks=[_observe(cache, lambda s: [Observation(s.limit_bytes)])],
        unit="By",
        description="STORAGE_HARD_LIMIT_BYTES (0 = no limit)",
    )
    meter.create_observable_gauge(
        "firebim.storage.objects",
        callbacks=[_observe(cache, lambda s: [Observation(s.object_count)])],
        unit="{object}",
        description="Objects stored in the bucket",
    )
    meter.create_observable_gauge(
        "firebim.storage.prefix.used",
        callbacks=[
            _observe(cache, lambda s: [Observation(used, {"prefix": prefix}) for prefix, used in s.by_prefix.items()])
        ],
        unit="By",
        description="Bytes stored per top-level key prefix",
    )
    meter.create_observable_gauge(
        "firebim.users",
        callbacks=[
            _observe(cache, lambda s: [Observation(count, {"state": state}) for state, count in s.users.items()])
        ],
        unit="{user}",
        description="Registered users (total) and users seen in the last 7 days (active_7d)",
    )
    if provider is None:
        _usage_cache = cache
    return cache


__all__ = [
    "UsageSnapshot",
    "UsageSnapshotCache",
    "bind_meter_provider",
    "load_usage_snapshot",
    "record_celery_task",
    "record_rate_limited",
    "record_storage_rejected",
    "record_storage_used",
    "register_usage_gauges",
]
