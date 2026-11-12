"""Throttled "last seen" tracking for authenticated users (``user_activity`` table).

Each process writes ``user_activity.last_seen_at`` for a user at most once per ACTIVITY_INTERVAL_SECONDS, so the
value is at most that stale. The throttle is an in-process LRU of user id -> last write; with several replicas each one
writes on its own schedule, which is still bounded. Errors are logged and swallowed: tracking never fails a request.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models import database
from app.models.models import UserActivity

logger = logging.getLogger(__name__)

ACTIVITY_INTERVAL_SECONDS = 300.0
ACTIVITY_TABLE = UserActivity.__table__


class ActivityThrottle:
    """Bounded ``user_id -> monotonic time of the last write``; thread-safe."""

    def __init__(
        self,
        *,
        interval_seconds: float = ACTIVITY_INTERVAL_SECONDS,
        max_entries: int = 50_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._last: OrderedDict[str, float] = OrderedDict()
        self._interval = interval_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()

    def claim(self, user_id: str) -> bool:
        """True when ``user_id`` is due for a write; the caller then owns that write for the next interval."""
        now = self._clock()
        with self._lock:
            last = self._last.get(user_id)
            if last is not None and now - last < self._interval:
                return False
            self._last[user_id] = now
            self._last.move_to_end(user_id)
            while len(self._last) > self._max_entries:
                self._last.popitem(last=False)
            return True

    def clear(self) -> None:
        with self._lock:
            self._last.clear()


_throttle = ActivityThrottle()


def upsert_last_seen(engine: Engine, user_id: str, seen_at: datetime) -> None:
    """``INSERT ... ON CONFLICT (user_id) DO UPDATE SET last_seen_at = :seen_at``."""
    values = {"user_id": user_id, "last_seen_at": seen_at}
    with Session(engine) as session:
        dialect = engine.dialect.name
        if dialect in {"postgresql", "sqlite"}:
            stmt = (pg_insert if dialect == "postgresql" else sqlite_insert)(ACTIVITY_TABLE).values(**values)
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["user_id"], set_={"last_seen_at": stmt.excluded.last_seen_at}
                )
            )
        else:
            exists = session.execute(
                select(ACTIVITY_TABLE.c.user_id).where(ACTIVITY_TABLE.c.user_id == user_id)
            ).first()
            if exists is None:
                session.execute(insert(ACTIVITY_TABLE).values(**values))
            else:
                session.execute(
                    update(ACTIVITY_TABLE).where(ACTIVITY_TABLE.c.user_id == user_id).values(last_seen_at=seen_at)
                )
        session.commit()


def _write(user_id: str) -> None:
    try:
        upsert_last_seen(database.engine, user_id, datetime.now(UTC))
    except Exception:
        # Not retried before the next interval, so a database outage does not turn into a write per request.
        logger.warning("Could not record activity for user %s", user_id, exc_info=True)


async def record_user_activity(user_id: str | None) -> None:
    """Update the user's ``last_seen_at`` if the last write from this process is older than the interval."""
    if not user_id:
        return
    try:
        if not _throttle.claim(str(user_id)):
            return
        await run_in_threadpool(_write, str(user_id))
    except Exception:
        logger.warning("Activity tracking failed", exc_info=True)


def reset_activity_throttle() -> None:
    """Forget every throttled user (tests)."""
    _throttle.clear()


__all__ = [
    "ACTIVITY_INTERVAL_SECONDS",
    "ActivityThrottle",
    "record_user_activity",
    "reset_activity_throttle",
    "upsert_last_seen",
]
