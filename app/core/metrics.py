"""Application metric hooks.

The hardening code calls these functions at the points worth counting. They are deliberately no-ops for now;
the OpenTelemetry branch replaces the bodies (``firebim.ratelimit.rejected``, ``firebim.storage.rejected``, ...)
without touching the callers. Keep them cheap, synchronous and exception-free.
"""

from __future__ import annotations


def record_rate_limited(route_class: str) -> None:
    """A request was rejected with 429. ``route_class`` is ``default``, ``expensive`` or ``anon``."""


def record_storage_rejected(reason: str) -> None:
    """A write was refused by a storage or size limit (e.g. ``payload_too_large``, ``storage_limit``)."""


def record_storage_used(*, used_bytes: int, object_count: int, limit_bytes: int) -> None:
    """Current bucket usage, reported after accounting updates or a reconcile."""


__all__ = ["record_rate_limited", "record_storage_rejected", "record_storage_used"]
