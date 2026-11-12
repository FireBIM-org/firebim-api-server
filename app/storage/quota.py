"""Storage accounting and the hard bucket cap.

Every object written to or deleted from the bucket goes through this module so that ``storage_usage`` (one global
row), ``storage_usage_prefix`` and ``storage_usage_user`` track what the bucket holds. A write first *reserves* its
bytes with an atomic conditional update::

    UPDATE storage_usage SET used_bytes = used_bytes + :delta, ...
    WHERE scope = 'global' AND used_bytes + :delta <= :limit

so concurrent writers (API replicas and Celery workers) can never push the total past ``STORAGE_HARD_LIMIT_BYTES``.
A refused write raises :class:`StorageLimitExceeded` / :class:`UserQuotaExceeded` (507) before anything reaches S3.
If the accounting database cannot be reached the write is refused with :class:`StorageAccountingUnavailable` (503):
the cap fails closed. A failed upload releases its reservation, and deletes decrement the counters (never below 0).

Helpers, from high to low level:

- :func:`account_write` — context manager around one ``put_object``/``upload_fileobj``; HEADs the key so an overwrite
  only accounts the size difference, and releases the reservation if the body raises.
- :func:`delete_object_accounted` / :func:`record_delete` — delete paths.
- :func:`reserve_upload` — reserve *before* handing out a presigned PUT URL (the bytes then bypass the API). Call
  ``reservation.release()`` if the upload is abandoned or expires; the reconcile job corrects any remaining drift.
- :func:`reserve` / :class:`Reservation` — the primitive the others build on.

The counters are kept honest by ``python -m app.storage.reconcile`` (see app/storage/reconcile.py), which recomputes
them from a bucket listing and carries over what the counters gained while the listing ran. Small drift between
reconciles is expected. Most of it over-counts: two concurrent first writes of the same key, a write counted both by
the listing and by the carry-over, or a reservation released after a reconcile (the release is then skipped rather
than subtracted from totals that never held it). It can under-count in one case: an upload already in flight when a
reconcile starts that finishes after the listing has passed its key is missing until the next reconcile, so the cap
can be exceeded by at most the bytes in flight at that moment.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import case, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlmodel import SQLModel

from app.core import metrics
from app.core.config import settings
from app.core.errors import ApiError
from app.models import database
from app.models.models import StorageUsage, StorageUsagePrefix, StorageUsageUser, UserActivity
from app.schemas.storage import StoragePrefixUsage, StorageSummary, StorageUserUsage

logger = logging.getLogger(__name__)

GLOBAL_SCOPE = "global"
# Top-level key prefixes tracked in storage_usage_prefix; every other key counts as OTHER_PREFIX.
STORAGE_PREFIXES = (
    "models",
    "ids",
    "graphs",
    "results",
    "checks",
    "lexicon",
    "rulebuilder",
    "ontologies",
    "ontology-sync",
)
OTHER_PREFIX = "other"
# Roots of UserScopedS3JsonStore documents: ``{root}/{quote(user_id)}/{quote(document_id)}.json``.
# Keep in sync with the repositories' ROOT_PREFIX constants (tests/test_storage_quota.py checks this).
USER_SCOPED_ROOTS = (
    "lexicon/handshakes",
    "lexicon/mappings",
    "lexicon/profiles",
    "ontologies/candidates/dictionaries",
    "rulebuilder/workspaces",
    "rulebuilder/rule-element-groups",
)
ACCOUNTING_TABLES = (
    StorageUsage.__table__,
    StorageUsageUser.__table__,
    StorageUsagePrefix.__table__,
    UserActivity.__table__,
)

GLOBAL_TABLE = StorageUsage.__table__
USER_TABLE = StorageUsageUser.__table__
PREFIX_TABLE = StorageUsagePrefix.__table__
_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


class StorageWriteRefused(ApiError):
    """A write was refused by storage accounting; raised before anything is sent to S3."""

    reason = "storage_limit"


class StorageLimitExceeded(StorageWriteRefused):
    reason = "storage_limit"

    def __init__(self, detail: str = "Storage limit reached: the write would exceed the bucket's hard limit") -> None:
        super().__init__(507, detail, "STORAGE_LIMIT_EXCEEDED")


class UserQuotaExceeded(StorageWriteRefused):
    reason = "user_quota"

    def __init__(self, detail: str = "Storage quota reached: the write would exceed your storage quota") -> None:
        super().__init__(507, detail, "USER_QUOTA_EXCEEDED")


class StorageAccountingUnavailable(StorageWriteRefused):
    reason = "accounting_unavailable"

    def __init__(self, detail: str = "Storage accounting is unavailable; the write was not attempted") -> None:
        super().__init__(503, detail, "STORAGE_ACCOUNTING_UNAVAILABLE")


# --- key layout ------------------------------------------------------------------------------------------------------


def prefix_for_key(key: str) -> str:
    """The storage_usage_prefix bucket of an object key (its first segment, or ``other``)."""
    head = str(key).lstrip("/").split("/", 1)[0]
    return head if head in STORAGE_PREFIXES else OTHER_PREFIX


def user_id_from_scoped_key(key: str) -> str | None:
    """The owner of a user-scoped JSON document key, or None for any other key."""
    for root in USER_SCOPED_ROOTS:
        if key.startswith(root + "/"):
            user_segment, sep, rest = key[len(root) + 1 :].partition("/")
            if user_segment and sep and rest:
                return unquote(user_segment)
    return None


# --- engine ----------------------------------------------------------------------------------------------------------

_engine_override: Engine | None = None
_tables_ready: set[int] = set()


def set_accounting_engine(engine: Engine | None) -> None:
    """Use ``engine`` for accounting instead of the application engine (tests, tools); None restores the default."""
    global _engine_override
    _engine_override = engine
    _tables_ready.clear()


def accounting_engine() -> Engine:
    return _engine_override if _engine_override is not None else database.engine


def ensure_accounting_tables(engine: Engine | None = None) -> None:
    """Create the accounting tables if missing (the API's create_all normally has; workers and the CLI may start
    first). Runs once per engine and process."""
    engine = engine or accounting_engine()
    if id(engine) in _tables_ready:
        return
    SQLModel.metadata.create_all(engine, tables=list(ACCOUNTING_TABLES))
    _tables_ready.add(id(engine))


# --- counters --------------------------------------------------------------------------------------------------------


def _clamped_add(column, delta: int):
    """``column + delta``, never below zero."""
    if delta >= 0:
        return column + delta
    return case((column + delta < 0, 0), else_=column + delta)


def ensure_counter_row(session: Session, table, **key) -> None:
    """Insert a zeroed counter row unless it already exists (``INSERT ... ON CONFLICT DO NOTHING``)."""
    values = {"used_bytes": 0, "object_count": 0, **key}
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        session.execute(pg_insert(table).values(**values).on_conflict_do_nothing())
    elif dialect == "sqlite":
        session.execute(sqlite_insert(table).values(**values).on_conflict_do_nothing())
    else:
        criteria = [table.c[name] == value for name, value in key.items()]
        if session.execute(select(*table.primary_key.columns).where(*criteria)).first() is None:
            session.execute(insert(table).values(**values))


def _normalize_user_id(user_id: str | None) -> str | None:
    return (str(user_id).strip() or None) if user_id is not None else None


# Sentinel for "apply whether or not a reconcile ran" (None is a meaningful last_reconciled_at).
_ANY_EPOCH = object()


def _apply(
    *,
    delta_bytes: int,
    object_delta: int,
    user_id: str | None,
    prefix: str,
    enforce: bool,
    only_if_reconciled_at=_ANY_EPOCH,
) -> tuple[int, int, datetime | None] | None:
    """Add the deltas to the global, user and prefix rows in one transaction; return the new global totals and the
    global row's ``last_reconciled_at``.

    With ``enforce`` a positive delta is conditional on the hard limit (and the user quota): if the update matches no
    row, the transaction is rolled back and the matching StorageWriteRefused subclass is raised. With
    ``only_if_reconciled_at`` nothing changes, and None is returned, when a reconcile has run since that value was
    read. Rows are always touched in the same order (global, user, prefix), so concurrent writers cannot deadlock."""
    engine = accounting_engine()
    ensure_accounting_tables(engine)
    now = datetime.now(UTC)
    limit = settings.STORAGE_HARD_LIMIT_BYTES
    user_quota = settings.STORAGE_USER_QUOTA_BYTES
    check = enforce and delta_bytes > 0

    with Session(engine) as session:
        ensure_counter_row(session, GLOBAL_TABLE, scope=GLOBAL_SCOPE)
        if user_id:
            ensure_counter_row(session, USER_TABLE, user_id=user_id)
        ensure_counter_row(session, PREFIX_TABLE, prefix=prefix)

        statement = (
            update(GLOBAL_TABLE)
            .where(GLOBAL_TABLE.c.scope == GLOBAL_SCOPE)
            .values(
                used_bytes=_clamped_add(GLOBAL_TABLE.c.used_bytes, delta_bytes),
                object_count=_clamped_add(GLOBAL_TABLE.c.object_count, object_delta),
                updated_at=now,
            )
            .returning(GLOBAL_TABLE.c.used_bytes, GLOBAL_TABLE.c.object_count, GLOBAL_TABLE.c.last_reconciled_at)
        )
        if check and limit > 0:
            statement = statement.where(GLOBAL_TABLE.c.used_bytes + delta_bytes <= limit)
        if only_if_reconciled_at is not _ANY_EPOCH:
            reconciled = GLOBAL_TABLE.c.last_reconciled_at
            statement = statement.where(
                reconciled.is_(None) if only_if_reconciled_at is None else reconciled == only_if_reconciled_at
            )
        totals = session.execute(statement).first()
        if totals is None:
            session.rollback()
            if only_if_reconciled_at is not _ANY_EPOCH:
                return None
            raise StorageLimitExceeded()

        if user_id:
            statement = (
                update(USER_TABLE)
                .where(USER_TABLE.c.user_id == user_id)
                .values(
                    used_bytes=_clamped_add(USER_TABLE.c.used_bytes, delta_bytes),
                    object_count=_clamped_add(USER_TABLE.c.object_count, object_delta),
                    updated_at=now,
                )
            )
            if check and user_quota > 0:
                statement = statement.where(USER_TABLE.c.used_bytes + delta_bytes <= user_quota)
            if session.execute(statement).rowcount == 0:
                session.rollback()
                raise UserQuotaExceeded()

        session.execute(
            update(PREFIX_TABLE)
            .where(PREFIX_TABLE.c.prefix == prefix)
            .values(
                used_bytes=_clamped_add(PREFIX_TABLE.c.used_bytes, delta_bytes),
                object_count=_clamped_add(PREFIX_TABLE.c.object_count, object_delta),
            )
        )
        session.commit()
    return int(totals[0]), int(totals[1]), totals[2]


def report_usage(used_bytes: int, object_count: int, *, previous_bytes: int | None = None) -> None:
    """Publish the usage metric and warn once when a write crosses STORAGE_WARN_RATIO of the hard limit."""
    limit = settings.STORAGE_HARD_LIMIT_BYTES
    metrics.record_storage_used(used_bytes=used_bytes, object_count=object_count, limit_bytes=limit)
    if limit <= 0 or previous_bytes is None:
        return
    threshold = limit * settings.STORAGE_WARN_RATIO
    if previous_bytes < threshold <= used_bytes:
        logger.warning(
            "Storage usage crossed the warning threshold: %d of %d bytes (%.1f%%)",
            used_bytes,
            limit,
            100.0 * used_bytes / limit,
        )


def _limits_enabled() -> bool:
    return settings.STORAGE_HARD_LIMIT_BYTES > 0 or settings.STORAGE_USER_QUOTA_BYTES > 0


@dataclass
class Reservation:
    """Bytes and objects added to the counters by :func:`reserve`; ``release()`` takes them back (idempotent).

    ``reconciled_at`` is the global ``last_reconciled_at`` when the reservation was made. If a reconcile has run since,
    the counters were rebuilt from the bucket (plus the reconcile's carry-over), so ``release()`` leaves them alone:
    subtracting the reservation again could only under-count."""

    delta_bytes: int
    object_delta: int
    prefix: str
    user_id: str | None = None
    applied: bool = True
    reconciled_at: datetime | None = None

    def release(self) -> None:
        if not self.applied:
            return
        self.applied = False
        adjust(
            -self.delta_bytes,
            object_delta=-self.object_delta,
            prefix=self.prefix,
            user_id=self.user_id,
            only_if_reconciled_at=self.reconciled_at,
        )


def reserve(delta_bytes: int, *, prefix: str, user_id: str | None = None, object_delta: int = 1) -> Reservation:
    """Atomically add ``delta_bytes`` (and ``object_delta`` objects) to the counters, or refuse the write.

    Raises StorageLimitExceeded / UserQuotaExceeded (507) when the global hard limit / the per-user quota would be
    exceeded, and StorageAccountingUnavailable (503) when the accounting database fails (fail closed). With both
    limits disabled (0) an accounting failure is only logged. A negative delta (an overwrite with a smaller object)
    is never refused."""
    prefix = prefix if prefix in STORAGE_PREFIXES else OTHER_PREFIX
    user_id = _normalize_user_id(user_id)
    try:
        used, objects, reconciled_at = _apply(
            delta_bytes=delta_bytes, object_delta=object_delta, user_id=user_id, prefix=prefix, enforce=True
        )
    except StorageWriteRefused as exc:
        metrics.record_storage_rejected(exc.reason)
        logger.warning(
            "Storage write refused (%s): %d bytes under %s for user %s", exc.reason, delta_bytes, prefix, user_id
        )
        raise
    except SQLAlchemyError as exc:
        if not _limits_enabled():
            logger.warning("Storage accounting unavailable; limits are disabled so the write proceeds", exc_info=exc)
            return Reservation(delta_bytes, object_delta, prefix, user_id, applied=False)
        metrics.record_storage_rejected(StorageAccountingUnavailable.reason)
        logger.exception("Storage accounting unavailable; refusing the write")
        raise StorageAccountingUnavailable() from exc
    report_usage(used, objects, previous_bytes=used - delta_bytes)
    return Reservation(delta_bytes, object_delta, prefix, user_id, reconciled_at=reconciled_at)


def adjust(
    delta_bytes: int,
    *,
    prefix: str,
    user_id: str | None = None,
    object_delta: int = 0,
    only_if_reconciled_at=_ANY_EPOCH,
) -> None:
    """Add to the counters (clamped at 0). Never raises: on a database error the change is logged and left for the
    next reconcile (an unrecorded decrement only over-counts, which is the safe direction).

    With ``only_if_reconciled_at`` the change is skipped when a reconcile has run since that ``last_reconciled_at``."""
    prefix = prefix if prefix in STORAGE_PREFIXES else OTHER_PREFIX
    user_id = _normalize_user_id(user_id)
    try:
        applied = _apply(
            delta_bytes=delta_bytes,
            object_delta=object_delta,
            user_id=user_id,
            prefix=prefix,
            enforce=False,
            only_if_reconciled_at=only_if_reconciled_at,
        )
    except Exception:
        logger.exception(
            "Storage accounting update failed (%d bytes under %s); reconcile will correct it", delta_bytes, prefix
        )
        return
    if applied is None:
        logger.info(
            "Storage counters were reconciled since the reservation; %d bytes left to the reconcile", delta_bytes
        )
        return
    used, objects, _ = applied
    report_usage(used, objects)


def record_delete(key: str, size: int, *, user_id: str | None = None) -> None:
    """Account an object that was deleted from the bucket."""
    adjust(-max(int(size), 0), prefix=prefix_for_key(key), user_id=user_id, object_delta=-1)


def reserve_upload(key: str, size: int, *, user_id: str | None = None) -> Reservation:
    """Reserve ``size`` bytes for a new object at ``key`` that will be uploaded later.

    Use this before issuing a presigned PUT URL (or any upload that bypasses the API) and sign the URL with the same
    ``ContentLength`` so the client cannot upload more than was reserved. If the upload is abandoned, call
    ``reservation.release()`` (e.g. from the upload cleanup job); otherwise the next reconcile drops it."""
    return reserve(max(int(size), 0), prefix=prefix_for_key(key), user_id=user_id, object_delta=1)


# --- S3 helpers ------------------------------------------------------------------------------------------------------


def existing_object_size(client, bucket: str, key: str) -> int | None:
    """Size of the object at ``key``, or None when it does not exist.

    Any other HEAD failure also returns None: the write is then accounted as a new object of its full size, which can
    only over-count."""
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code", "")) not in _NOT_FOUND_CODES:
            logger.warning("Could not HEAD %s before writing it; accounting it as a new object", key)
        return None
    except BotoCoreError:
        logger.warning("Could not HEAD %s before writing it; accounting it as a new object", key)
        return None
    size = head.get("ContentLength") if isinstance(head, dict) else None
    return size if isinstance(size, int) and size >= 0 else 0


@contextmanager
def account_write(
    client, bucket: str, key: str, size: int, *, user_id: str | None = None, new_object: bool = False
) -> Iterator[Reservation]:
    """Reserve the bytes for writing ``size`` bytes at ``key`` around the actual write::

        with account_write(client, bucket, key, len(body), user_id=user.id):
            client.put_object(Bucket=bucket, Key=key, Body=body)

    An existing object at ``key`` is HEADed so an overwrite only reserves the difference (``new_object=True`` skips
    the HEAD for freshly generated keys). The reservation is released if the block raises."""
    previous = None if new_object else existing_object_size(client, bucket, key)
    reservation = reserve(
        int(size) - (previous or 0),
        prefix=prefix_for_key(key),
        user_id=user_id,
        object_delta=0 if previous is not None else 1,
    )
    try:
        yield reservation
    except BaseException:
        reservation.release()
        raise


def delete_object_accounted(client, bucket: str, key: str, *, user_id: str | None = None) -> bool:
    """Delete ``key`` and decrement the counters by its size. Returns False when the object did not exist.

    S3 errors from the delete propagate; the counters only change after a successful delete."""
    size = existing_object_size(client, bucket, key)
    client.delete_object(Bucket=bucket, Key=key)
    if size is None:
        return False
    record_delete(key, size, user_id=user_id)
    return True


# --- reads -----------------------------------------------------------------------------------------------------------


def get_storage_summary(engine: Engine | None = None) -> StorageSummary:
    """Current usage in the StorageSummary shape (admin API, metrics, reconcile CLI). Raises on database errors."""
    engine = engine or accounting_engine()
    ensure_accounting_tables(engine)
    with Session(engine) as session:
        row = session.execute(select(GLOBAL_TABLE).where(GLOBAL_TABLE.c.scope == GLOBAL_SCOPE)).first()
        prefixes = session.execute(
            select(PREFIX_TABLE).order_by(PREFIX_TABLE.c.used_bytes.desc(), PREFIX_TABLE.c.prefix)
        ).all()
    used = int(row.used_bytes) if row else 0
    objects = int(row.object_count) if row else 0
    last_reconciled_at = row.last_reconciled_at if row else None
    if last_reconciled_at is not None and last_reconciled_at.tzinfo is None:
        last_reconciled_at = last_reconciled_at.replace(tzinfo=UTC)  # sqlite drops the zone
    limit = settings.STORAGE_HARD_LIMIT_BYTES
    return StorageSummary(
        used_bytes=used,
        object_count=objects,
        limit_bytes=limit,
        user_quota_bytes=settings.STORAGE_USER_QUOTA_BYTES,
        usage_ratio=(used / limit) if limit > 0 else 0.0,
        warn_ratio=settings.STORAGE_WARN_RATIO,
        last_reconciled_at=last_reconciled_at,
        by_prefix=[
            StoragePrefixUsage(prefix=p.prefix, bytes=int(p.used_bytes), objects=int(p.object_count)) for p in prefixes
        ],
    )


def get_top_users(limit: int = 10, engine: Engine | None = None) -> list[StorageUserUsage]:
    """Users with the most attributed bytes, largest first."""
    engine = engine or accounting_engine()
    ensure_accounting_tables(engine)
    with Session(engine) as session:
        rows = session.execute(
            select(USER_TABLE).order_by(USER_TABLE.c.used_bytes.desc(), USER_TABLE.c.user_id).limit(limit)
        ).all()
    return [StorageUserUsage(user_id=r.user_id, bytes=int(r.used_bytes), objects=int(r.object_count)) for r in rows]


def get_user_usage(user_id: str, engine: Engine | None = None) -> StorageUserUsage:
    engine = engine or accounting_engine()
    ensure_accounting_tables(engine)
    with Session(engine) as session:
        row = session.execute(select(USER_TABLE).where(USER_TABLE.c.user_id == user_id)).first()
    if row is None:
        return StorageUserUsage(user_id=user_id, bytes=0, objects=0)
    return StorageUserUsage(user_id=user_id, bytes=int(row.used_bytes), objects=int(row.object_count))


__all__ = [
    "ACCOUNTING_TABLES",
    "GLOBAL_SCOPE",
    "GLOBAL_TABLE",
    "PREFIX_TABLE",
    "USER_TABLE",
    "OTHER_PREFIX",
    "STORAGE_PREFIXES",
    "USER_SCOPED_ROOTS",
    "Reservation",
    "StorageAccountingUnavailable",
    "StorageLimitExceeded",
    "StorageWriteRefused",
    "UserQuotaExceeded",
    "account_write",
    "accounting_engine",
    "adjust",
    "delete_object_accounted",
    "ensure_accounting_tables",
    "ensure_counter_row",
    "existing_object_size",
    "get_storage_summary",
    "get_top_users",
    "get_user_usage",
    "prefix_for_key",
    "record_delete",
    "report_usage",
    "reserve",
    "reserve_upload",
    "set_accounting_engine",
    "user_id_from_scoped_key",
]
