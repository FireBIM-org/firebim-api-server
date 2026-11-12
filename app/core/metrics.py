"""Application metric hooks.

The hardening code calls these functions at the points worth counting. They are deliberately no-ops for now;
the OpenTelemetry branch replaces the bodies (``firebim.ratelimit.rejected``, ``firebim.storage.rejected``, ...)
without touching the callers. Keep them cheap, synchronous and exception-free.

Only the counters belong here. The storage and user gauges (``firebim.storage.used``/``limit``/``objects``,
``firebim.storage.prefix.used{prefix}``, ``firebim.users{state}``) should be observable gauges whose callbacks read
``app.storage.quota.get_storage_summary()`` and the user counts from the database (cached for about a minute), so
every replica reports the same current value; ``record_storage_used`` only sees the totals after a write or reconcile
in its own process, which go stale after deletes elsewhere.
"""

from __future__ import annotations


def record_rate_limited(route_class: str) -> None:
    """A request was rejected with 429. ``route_class`` is ``default``, ``expensive`` or ``anon``."""


def record_storage_rejected(reason: str) -> None:
    """A write was refused by a storage or size limit (e.g. ``payload_too_large``, ``storage_limit``)."""


def record_storage_used(*, used_bytes: int, object_count: int, limit_bytes: int) -> None:
    """Bucket usage seen by this process after an accounting update or a reconcile (a hint, not the gauge source)."""


__all__ = ["record_rate_limited", "record_storage_rejected", "record_storage_used"]
