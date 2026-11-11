from __future__ import annotations

import logging
import os
from time import monotonic

from alembic.config import Config
from sqlalchemy import event, inspect
from sqlmodel import Session, create_engine

from alembic import command
from app.core.config import settings
from app.core.observability import (
    clear_db_checkout_wait,
    finish_db_checkout_wait,
    is_request_profiling_enabled,
    log_event,
    record_metric,
)

# Get PostgreSQL connection details from environment variables
POSTGRES_USER = settings.POSTGRES_USER
POSTGRES_PASSWORD = settings.POSTGRES_PASSWORD.get_secret_value()
POSTGRES_HOST = settings.POSTGRES_HOST
POSTGRES_PORT = settings.POSTGRES_PORT
POSTGRES_DB = settings.POSTGRES_DB

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# Create the SQLModel engine
logger = logging.getLogger(__name__)

# Create the SQLModel engine
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)


def _pool_stats() -> dict[str, int | str]:
    pool = engine.pool
    stats: dict[str, int | str] = {"status": pool.status()}
    for name in ("size", "checkedout", "overflow"):
        attr = getattr(pool, name, None)
        if callable(attr):
            try:
                stats[name] = int(attr())
            except Exception:
                continue
    return stats


@event.listens_for(engine.pool, "checkout")
def _on_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
    pending = finish_db_checkout_wait()
    wait_ms = None
    label = None
    if pending is not None:
        label, wait_ms = pending
        record_metric(f"db_checkout_wait_ms.{label}", wait_ms, append=True)
        record_metric("db_checkout_wait_ms_total", wait_ms, accumulate=True)

    if is_request_profiling_enabled():
        log_event(
            logger,
            "db.pool.checkout",
            checkout_label=label,
            checkout_wait_ms=wait_ms,
            **_pool_stats(),
        )


@event.listens_for(engine.pool, "checkin")
def _on_checkin(dbapi_connection, connection_record) -> None:
    if is_request_profiling_enabled():
        log_event(logger, "db.pool.checkin", **_pool_stats())


def _session_scope(scope_name: str):
    """
    Dependency for database session management.
    Creates a new database session for each request, and closes it when the request is finished.
    """
    started_at = monotonic()
    session = Session(engine)
    try:
        if is_request_profiling_enabled():
            log_event(logger, "db.session.open", session_scope=scope_name)
        yield session
    finally:
        session.close()
        clear_db_checkout_wait()
        duration_ms = round((monotonic() - started_at) * 1000, 3)
        record_metric(f"db_session_ms.{scope_name}", duration_ms, append=True)
        if is_request_profiling_enabled():
            log_event(logger, "db.session.close", session_scope=scope_name, duration_ms=duration_ms)


def get_db():
    yield from _session_scope("request")


def get_auth_db():
    yield from _session_scope("auth")


# Function to initialize the database
def init_db():
    """Bootstrap an empty database; existing deployments must migrate before startup."""
    from app.models import models

    with engine.begin() as connection:
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        if not existing:
            models.SQLModel.metadata.create_all(bind=connection)
            return
        missing = []
        for table in models.SQLModel.metadata.sorted_tables:
            if table.name not in existing:
                missing.append(table.name)
                continue
            columns = {column["name"] for column in inspector.get_columns(table.name)}
            missing.extend(f"{table.name}.{name}" for name in sorted(set(table.c.keys()) - columns))
        if missing:
            raise RuntimeError(
                f"Database schema is incomplete (missing: {', '.join(missing)}). "
                "Run `alembic upgrade head` before starting the API. No schema changes were made."
            )


def run_migrations():
    """Run alembic migrations programmatically"""
    logger.info("Running database migrations")

    # Calculate the path to alembic.ini relative to this file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # database.py is in app/models/, so we go up two levels to reach firebim-api-server/
    root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
    ini_path = os.path.join(root_dir, "alembic.ini")

    if not os.path.exists(ini_path):
        logger.error(f"alembic.ini not found at {ini_path}")
        # Fallback to current working directory
        ini_path = "alembic.ini"
        if not os.path.exists(ini_path):
            raise FileNotFoundError("Could not find alembic.ini")

    alembic_cfg = Config(ini_path)
    command.upgrade(alembic_cfg, "head")
    logger.info("Database migrations completed successfully")
