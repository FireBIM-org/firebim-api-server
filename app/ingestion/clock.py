"""Use PostgreSQL wall time across API, dispatcher and worker hosts."""

from datetime import UTC, datetime

from sqlalchemy import func
from sqlmodel import select


def database_now(db, fallback=lambda: datetime.now(UTC)):
    if db.get_bind().dialect.name == "postgresql":
        # now() is frozen at transaction start, including while waiting for locks
        # or executing a publication callback. Leases need the current wall time.
        return db.exec(select(func.clock_timestamp())).one()
    return fallback()
