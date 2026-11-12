"""Recompute the storage accounting tables from the bucket itself.

    python -m app.storage.reconcile

lists every object with ``list_objects_v2`` (paginated), totals bytes and objects globally, per top-level prefix and
per user, and writes those values (plus ``last_reconciled_at``) to ``storage_usage``, ``storage_usage_prefix`` and
``storage_usage_user``. It prints the resulting StorageSummary as JSON and exits 0, or
exits non-zero on failure (2 when another reconcile holds the lock). Deploy runs it as a CronJob; the API runs it
once in the background at startup when the bucket has never been reconciled, and the admin API calls
:func:`reconcile_storage` directly.

User attribution: ``models/`` and ``ids/`` keys via the ``ifc_models`` / ``ids_files`` rows (s3_key or the id
segment), ``results/{model_id}/`` via the model's owner, user-scoped JSON documents via the user segment of the key
and ontology sync jobs via the ``owner_id`` in the job document. Everything else (graphs, manifests, check results)
is global.

Writes keep going while the bucket is listed, and the listing may or may not include them. So the counters are read
before the listing starts, and when the totals are written (with the global row locked, which every accounting write
updates first) each row gets the listed value plus whatever its counter gained in the meantime (net, never
negative). A write during the listing is then counted at least once (twice if the listing saw it too, an over-count
the next reconcile settles), and :class:`app.storage.quota.Reservation` skips releases of reservations made before a
reconcile. What remains is an under-count by uploads that were already in flight when the listing started and finish
after it passed their key, until the next reconcile. Incomplete multipart uploads and non-current object versions are
not listed and therefore not counted.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core import s3 as core_s3
from app.models.models import IdsFile, IfcModel
from app.schemas.storage import StorageSummary
from app.storage import quota

logger = logging.getLogger(__name__)

# pg advisory lock key shared by every reconcile (CLI, startup, admin) so two replicas never run it at once.
RECONCILE_LOCK_KEY = 0x46424D53  # "FBMS"
SYNC_JOB_PREFIX = "ontology-sync/jobs/"


class ReconcileInProgressError(RuntimeError):
    """Another process holds the reconcile lock."""


@dataclass
class _Totals:
    bytes: int = 0
    objects: int = 0

    def add(self, size: int) -> None:
        self.bytes += size
        self.objects += 1


def list_bucket_objects(client, bucket: str, prefix: str = "") -> Iterator[tuple[str, int]]:
    """Yield ``(key, size)`` for every object in the bucket (under ``prefix``), following continuation tokens."""
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "MaxKeys": 1000}
        if prefix:
            kwargs["Prefix"] = prefix
        if token:
            kwargs["ContinuationToken"] = token
        response = client.list_objects_v2(**kwargs)
        for obj in response.get("Contents") or []:
            yield obj["Key"], int(obj.get("Size") or 0)
        if not response.get("IsTruncated"):
            return
        token = response.get("NextContinuationToken")
        if not token:
            raise RuntimeError("Bucket listing is truncated but has no continuation token")


class _Attribution:
    """Maps object keys to the user that owns them (None = global)."""

    def __init__(self, session: Session, client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket
        self.by_key: dict[str, str] = {}
        self.model_owner: dict[str, str] = {}
        self.ids_owner: dict[str, str] = {}
        for model_id, s3_key, result_key, user_id in session.execute(
            select(IfcModel.id, IfcModel.s3_key, IfcModel.result_key, IfcModel.user_id)
        ):
            self.model_owner[model_id] = user_id
            self.by_key[s3_key] = user_id
            if result_key:
                self.by_key[result_key] = user_id
        for ids_id, s3_key, user_id in session.execute(select(IdsFile.id, IdsFile.s3_key, IdsFile.user_id)):
            self.ids_owner[ids_id] = user_id
            self.by_key[s3_key] = user_id

    def user_for(self, key: str) -> str | None:
        if key in self.by_key:
            return self.by_key[key]
        head, _, rest = key.partition("/")
        object_id = rest.partition("/")[0]
        if head in {"models", "results"}:
            return self.model_owner.get(object_id)
        if head == "ids":
            return self.ids_owner.get(object_id)
        if key.startswith(SYNC_JOB_PREFIX):
            return self._sync_job_owner(key)
        return quota.user_id_from_scoped_key(key)

    def _sync_job_owner(self, key: str) -> str | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            owner = json.loads(response["Body"].read().decode("utf-8")).get("owner_id")
        except Exception:
            logger.warning("Could not read the owner of %s; counting it as global", key)
            return None
        return str(owner) if owner else None


@contextmanager
def _reconcile_lock(engine: Engine) -> Iterator[bool]:
    """A session-level pg advisory lock held for the whole run; other databases (sqlite in tests) need none."""
    if engine.dialect.name != "postgresql":
        yield True
        return
    with engine.connect() as connection:
        result = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": RECONCILE_LOCK_KEY})
        acquired = bool(result.scalar())
        connection.commit()  # the lock outlives the transaction; do not sit idle in one while listing
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": RECONCILE_LOCK_KEY})
                    connection.commit()
                except Exception:
                    connection.invalidate()  # closing the connection releases the lock
                    raise


def _last_reconciled_at(engine: Engine) -> datetime | None:
    with Session(engine) as session:
        return session.execute(
            select(quota.GLOBAL_TABLE.c.last_reconciled_at).where(quota.GLOBAL_TABLE.c.scope == quota.GLOBAL_SCOPE)
        ).scalar()


@dataclass
class _Counters:
    total: _Totals = field(default_factory=_Totals)
    by_prefix: dict[str, _Totals] = field(default_factory=dict)
    by_user: dict[str, _Totals] = field(default_factory=dict)


def _read_counters(session: Session, *, lock: bool = False) -> _Counters:
    """The accounting rows as they are now; ``lock`` holds the global row (SELECT ... FOR UPDATE) until commit."""
    statement = select(quota.GLOBAL_TABLE.c.used_bytes, quota.GLOBAL_TABLE.c.object_count).where(
        quota.GLOBAL_TABLE.c.scope == quota.GLOBAL_SCOPE
    )
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).first()
    prefix_table, user_table = quota.PREFIX_TABLE, quota.USER_TABLE
    return _Counters(
        total=_Totals(int(row.used_bytes), int(row.object_count)) if row else _Totals(),
        by_prefix={
            r.prefix: _Totals(int(r.used_bytes), int(r.object_count))
            for r in session.execute(
                select(prefix_table.c.prefix, prefix_table.c.used_bytes, prefix_table.c.object_count)
            )
        },
        by_user={
            r.user_id: _Totals(int(r.used_bytes), int(r.object_count))
            for r in session.execute(select(user_table.c.user_id, user_table.c.used_bytes, user_table.c.object_count))
        },
    )


def _with_carry_over(listed: _Totals | None, start: _Totals | None, current: _Totals | None) -> _Totals:
    """``listed`` plus what the counter gained between ``start`` (before the listing) and ``current`` (net, >= 0)."""
    listed, start, current = listed or _Totals(), start or _Totals(), current or _Totals()
    return _Totals(
        listed.bytes + max(0, current.bytes - start.bytes),
        listed.objects + max(0, current.objects - start.objects),
    )


def _write_totals(engine: Engine, *, listed: _Counters, start: _Counters, reconciled_at: datetime) -> _Counters:
    """Write the listed totals plus the carry-over since ``start``; return what was written."""
    with Session(engine) as session:
        quota.ensure_counter_row(session, quota.GLOBAL_TABLE, scope=quota.GLOBAL_SCOPE)
        # Every accounting write updates the global row first, so holding it keeps the carry-over exact until commit.
        current = _read_counters(session, lock=True)
        total = _with_carry_over(listed.total, start.total, current.total)
        by_prefix = {
            prefix: _with_carry_over(
                listed.by_prefix.get(prefix), start.by_prefix.get(prefix), current.by_prefix.get(prefix)
            )
            for prefix in (*quota.STORAGE_PREFIXES, quota.OTHER_PREFIX)
        }
        by_user: dict[str, _Totals] = {}
        for user_id in sorted(set(listed.by_user) | set(current.by_user)):
            totals = _with_carry_over(
                listed.by_user.get(user_id), start.by_user.get(user_id), current.by_user.get(user_id)
            )
            if totals.bytes or totals.objects:
                by_user[user_id] = totals
        session.execute(
            update(quota.GLOBAL_TABLE)
            .where(quota.GLOBAL_TABLE.c.scope == quota.GLOBAL_SCOPE)
            .values(
                used_bytes=total.bytes,
                object_count=total.objects,
                updated_at=reconciled_at,
                last_reconciled_at=reconciled_at,
            )
        )
        session.execute(delete(quota.PREFIX_TABLE))
        session.execute(
            insert(quota.PREFIX_TABLE),
            [
                {"prefix": prefix, "used_bytes": by_prefix[prefix].bytes, "object_count": by_prefix[prefix].objects}
                for prefix in (*quota.STORAGE_PREFIXES, quota.OTHER_PREFIX)
            ],
        )
        session.execute(delete(quota.USER_TABLE))
        if by_user:
            session.execute(
                insert(quota.USER_TABLE),
                [
                    {"user_id": user_id, "used_bytes": t.bytes, "object_count": t.objects, "updated_at": reconciled_at}
                    for user_id, t in by_user.items()
                ],
            )
        session.commit()
    return _Counters(total=total, by_prefix=by_prefix, by_user=by_user)


def reconcile_storage(
    *,
    s3_client=None,
    bucket: str | None = None,
    engine: Engine | None = None,
    only_if_never_reconciled: bool = False,
) -> StorageSummary | None:
    """List the bucket and rebuild the accounting tables from it (plus the carry-over); return the new summary.

    Raises ReconcileInProgressError if another process is reconciling. With ``only_if_never_reconciled`` it returns
    None without listing when the global row already has a ``last_reconciled_at``."""
    engine = engine or quota.accounting_engine()
    client = s3_client or core_s3.s3_client
    bucket = bucket or core_s3.BUCKET_NAME
    quota.ensure_accounting_tables(engine)

    with _reconcile_lock(engine) as acquired:
        if not acquired:
            raise ReconcileInProgressError("Another storage reconcile is running")
        if only_if_never_reconciled and _last_reconciled_at(engine) is not None:
            return None

        started = time.monotonic()
        with Session(engine) as session:
            attribution = _Attribution(session, client, bucket)
            start = _read_counters(session)
        listed = _Counters(by_prefix=defaultdict(_Totals), by_user=defaultdict(_Totals))
        for key, size in list_bucket_objects(client, bucket):
            listed.total.add(size)
            listed.by_prefix[quota.prefix_for_key(key)].add(size)
            user_id = attribution.user_for(key)
            if user_id:
                listed.by_user[user_id].add(size)

        written = _write_totals(engine, listed=listed, start=start, reconciled_at=datetime.now(UTC))

    summary = quota.get_storage_summary(engine)
    quota.report_usage(summary.used_bytes, summary.object_count)
    logger.info(
        "Storage reconciled: %d bytes in %d objects listed (%d bytes carried over from concurrent writes), "
        "%d users, in %.1fs",
        listed.total.bytes,
        listed.total.objects,
        written.total.bytes - listed.total.bytes,
        len(written.by_user),
        time.monotonic() - started,
    )
    return summary


def _startup_reconcile() -> None:
    try:
        if reconcile_storage(only_if_never_reconciled=True) is None:
            logger.info("Storage accounting already reconciled; startup reconcile skipped")
    except ReconcileInProgressError:
        logger.info("Storage reconcile already running elsewhere; startup reconcile skipped")
    except Exception:
        logger.exception("Startup storage reconcile failed; the counters stay as they are until the next reconcile")


def start_startup_reconcile() -> threading.Thread:
    """Run the first-ever reconcile in a daemon thread so API startup is never blocked by a bucket listing."""
    thread = threading.Thread(target=_startup_reconcile, name="storage-reconcile", daemon=True)
    thread.start()
    return thread


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        summary = reconcile_storage()
    except ReconcileInProgressError as exc:
        logger.error("%s", exc)
        return 2
    except Exception:
        logger.exception("Storage reconcile failed")
        return 1
    print(summary.model_dump_json())
    return 0


__all__ = [
    "RECONCILE_LOCK_KEY",
    "ReconcileInProgressError",
    "list_bucket_objects",
    "main",
    "reconcile_storage",
    "start_startup_reconcile",
]


if __name__ == "__main__":
    sys.exit(main())
