"""Admin API for the admin dashboard. Every route requires an admin (app/auth/admin.py); others get 403.

Per-user numbers come from one statement each: model and IDS counts are aggregated with GROUP BY in subqueries and
joined to ``users`` together with ``user_activity`` and ``storage_usage_user``, so listing users costs the same for
one or two hundred rows.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, false, func, or_, select, true
from sqlmodel import Session

from app.api.build_info import current_build_info
from app.api.health import dependency_checks
from app.auth.admin import known_admin_status, require_admin
from app.core import s3 as core_s3
from app.core.config import settings
from app.core.errors import ApiError
from app.models.database import get_db
from app.models.models import IdsFile, IfcModel, StorageUsageUser, User, UserActivity
from app.schemas.admin import (
    AdminContentCounts,
    AdminIdsFileSummary,
    AdminModelSummary,
    AdminOverview,
    AdminStorage,
    AdminStorageUser,
    AdminSystem,
    AdminSystemChecks,
    AdminSystemLimits,
    AdminUser,
    AdminUserCounts,
    AdminUserDetail,
    AdminUserList,
    AdminUserUpdate,
)
from app.schemas.storage import StoragePrefixUsage, StorageSummary
from app.storage import quota
from app.storage.reconcile import ReconcileInProgressError, list_bucket_objects, reconcile_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

ACTIVITY = UserActivity.__table__
USER_USAGE = StorageUsageUser.__table__
RECENT_ITEMS = 10
# Upper bound on S3 listings for one user's storage breakdown (6 user-scoped roots + one per model/IDS prefix).
MAX_BREAKDOWN_LISTINGS = 200

UserSort = Literal["created_at", "last_seen_at", "storage_bytes", "username"]
SortOrder = Literal["desc", "asc"]


def _utc(value: datetime | None) -> datetime | None:
    """sqlite drops the zone of timezone-aware columns; everything is stored in UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# --- users -----------------------------------------------------------------------------------------------------------


def _admin_user_columns():
    model_counts = (
        select(IfcModel.user_id.label("user_id"), func.count().label("n")).group_by(IfcModel.user_id).subquery()
    )
    ids_counts = select(IdsFile.user_id.label("user_id"), func.count().label("n")).group_by(IdsFile.user_id).subquery()
    storage_bytes = func.coalesce(USER_USAGE.c.used_bytes, 0)
    columns = {
        "id": User.id,
        "username": User.username,
        "email": User.email,
        "is_active": User.is_active,
        "created_at": User.created_at,
        "last_seen_at": ACTIVITY.c.last_seen_at,
        "ifc_models": func.coalesce(model_counts.c.n, 0),
        "ids_files": func.coalesce(ids_counts.c.n, 0),
        "storage_bytes": storage_bytes,
        "storage_objects": func.coalesce(USER_USAGE.c.object_count, 0),
    }
    statement = (
        select(*(column.label(name) for name, column in columns.items()))
        .select_from(User)
        .outerjoin(ACTIVITY, ACTIVITY.c.user_id == User.id)
        .outerjoin(model_counts, model_counts.c.user_id == User.id)
        .outerjoin(ids_counts, ids_counts.c.user_id == User.id)
        .outerjoin(USER_USAGE, USER_USAGE.c.user_id == User.id)
    )
    return statement, columns


def _admin_user(row) -> AdminUser:
    return AdminUser(
        id=row.id,
        username=row.username,
        email=row.email or "",
        is_active=bool(row.is_active),
        is_admin=known_admin_status(row.id),
        created_at=_utc(row.created_at),
        last_seen_at=_utc(row.last_seen_at),
        ifc_models=int(row.ifc_models),
        ids_files=int(row.ids_files),
        storage_bytes=int(row.storage_bytes),
        storage_objects=int(row.storage_objects),
    )


def _search_filter(search: str | None):
    term = (search or "").strip()
    if not term:
        return None
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return or_(User.username.ilike(pattern, escape="\\"), User.email.ilike(pattern, escape="\\"))


def _load_admin_user(db: Session, user_id: str) -> AdminUser | None:
    statement, _ = _admin_user_columns()
    row = db.exec(statement.where(User.id == user_id)).first()
    return _admin_user(row) if row is not None else None


@router.get("/users", response_model=AdminUserList)
def list_users(
    search: str | None = Query(None, max_length=200, description="Case-insensitive match on username or email"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: UserSort = "created_at",
    order: SortOrder = "desc",
    db: Session = Depends(get_db),
) -> AdminUserList:
    statement, columns = _admin_user_columns()
    criteria = _search_filter(search)
    total_statement = select(func.count()).select_from(User)
    if criteria is not None:
        statement = statement.where(criteria)
        total_statement = total_statement.where(criteria)

    sort_column = func.lower(User.username) if sort == "username" else columns[sort]
    direction = sort_column.desc() if order == "desc" else sort_column.asc()
    rows = db.exec(statement.order_by(direction.nulls_last(), User.id).limit(limit).offset(offset)).all()
    total = int(db.exec(total_statement).one()[0])
    return AdminUserList(items=[_admin_user(row) for row in rows], total=total)


def _user_storage_breakdown(
    user_id: str, model_ids: list[str], ids_ids: list[str]
) -> tuple[list[StoragePrefixUsage], bool]:
    """List the user's objects (user-scoped roots, then per-upload prefixes) and total them per top-level prefix."""
    user_segment = quote(user_id, safe="")
    prefixes = [f"{root}/{user_segment}/" for root in quota.USER_SCOPED_ROOTS]
    prefixes += [f"{head}/{model_id}/" for model_id in model_ids for head in ("models", "results")]
    prefixes += [f"ids/{ids_id}/" for ids_id in ids_ids]
    complete = len(prefixes) <= MAX_BREAKDOWN_LISTINGS
    totals: dict[str, list[int]] = {}
    client, bucket = core_s3.s3_client, core_s3.BUCKET_NAME
    for prefix in prefixes[:MAX_BREAKDOWN_LISTINGS]:
        for key, size in list_bucket_objects(client, bucket, prefix=prefix):
            entry = totals.setdefault(quota.prefix_for_key(key), [0, 0])
            entry[0] += size
            entry[1] += 1
    usage = [StoragePrefixUsage(prefix=name, bytes=value[0], objects=value[1]) for name, value in totals.items()]
    usage.sort(key=lambda item: (-item.bytes, item.prefix))
    return usage, complete


@router.get("/users/{user_id}", response_model=AdminUserDetail)
def get_user(user_id: str, db: Session = Depends(get_db)) -> AdminUserDetail:
    user = _load_admin_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    models = db.exec(
        select(IfcModel.id, IfcModel.filename, IfcModel.processed, IfcModel.processing_status, IfcModel.created_at)
        .where(IfcModel.user_id == user_id)
        .order_by(IfcModel.created_at.desc(), IfcModel.id)
    ).all()
    ids_files = db.exec(
        select(IdsFile.id, IdsFile.filename, IdsFile.created_at)
        .where(IdsFile.user_id == user_id)
        .order_by(IdsFile.created_at.desc(), IdsFile.id)
    ).all()

    try:
        by_prefix, complete = _user_storage_breakdown(user_id, [m.id for m in models], [i.id for i in ids_files])
    except Exception:
        logger.warning("Could not list the objects of user %s", user_id, exc_info=True)
        by_prefix, complete = [], False

    return AdminUserDetail(
        **user.model_dump(),
        recent_models=[
            AdminModelSummary(
                id=m.id,
                filename=m.filename,
                processed=bool(m.processed),
                processing_status=m.processing_status,
                created_at=_utc(m.created_at),
            )
            for m in models[:RECENT_ITEMS]
        ],
        recent_ids_files=[
            AdminIdsFileSummary(id=i.id, filename=i.filename, created_at=_utc(i.created_at))
            for i in ids_files[:RECENT_ITEMS]
        ],
        storage_by_prefix=by_prefix,
        storage_by_prefix_complete=complete,
    )


@router.patch("/users/{user_id}", response_model=AdminUser)
def update_user(
    user_id: str,
    body: AdminUserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AdminUser:
    """Activate or deactivate a user. A deactivated user gets 400 "Inactive user" on every authenticated route."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id and not body.is_active:
        raise ApiError(400, "Admins cannot deactivate themselves", "CANNOT_DEACTIVATE_SELF")
    if user.is_active != body.is_active:
        user.is_active = body.is_active
        db.add(user)
        db.commit()
        logger.info("Admin %s set is_active=%s for user %s", admin.id, body.is_active, user_id)
    return _load_admin_user(db, user_id)


# --- overview --------------------------------------------------------------------------------------------------------


def _count_when(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


@router.get("/overview", response_model=AdminOverview)
def overview(db: Session = Depends(get_db)) -> AdminOverview:
    now = datetime.now(UTC)
    week_ago, month_ago = now - timedelta(days=7), now - timedelta(days=30)

    active_7d = (
        select(func.count())
        .select_from(ACTIVITY)
        .join(User, User.id == ACTIVITY.c.user_id)
        .where(ACTIVITY.c.last_seen_at >= week_ago)
        .scalar_subquery()
    )
    users = db.exec(
        select(
            func.count(),
            _count_when(User.is_active == true()),
            _count_when(User.created_at >= week_ago),
            _count_when(User.created_at >= month_ago),
            active_7d,
        ).select_from(User)
    ).one()
    models = db.exec(
        select(
            func.count(),
            _count_when(and_(IfcModel.processed == false(), IfcModel.processing_status != "processing")),
            _count_when(and_(IfcModel.processed == false(), IfcModel.processing_status == "processing")),
            _count_when(IfcModel.processed == true()),
        ).select_from(IfcModel)
    ).one()
    ids_files = db.exec(select(func.count()).select_from(IdsFile)).one()[0]

    return AdminOverview(
        users=AdminUserCounts(
            total=int(users[0]),
            active=int(users[1]),
            new_7d=int(users[2]),
            new_30d=int(users[3]),
            active_7d=int(users[4]),
        ),
        content=AdminContentCounts(
            ifc_models=int(models[0]),
            ids_files=int(ids_files),
            models_pending=int(models[1]),
            models_processing=int(models[2]),
            models_processed=int(models[3]),
        ),
        storage=quota.get_storage_summary(),
        generated_at=now,
    )


# --- storage ---------------------------------------------------------------------------------------------------------


@router.get("/storage", response_model=AdminStorage)
def storage(
    top: int = Query(10, ge=1, le=100, description="Number of top_users"), db: Session = Depends(get_db)
) -> AdminStorage:
    summary = quota.get_storage_summary()
    rows = db.exec(
        select(USER_USAGE.c.user_id, User.username, User.email, USER_USAGE.c.used_bytes, USER_USAGE.c.object_count)
        .select_from(USER_USAGE)
        .outerjoin(User, User.id == USER_USAGE.c.user_id)
        .order_by(USER_USAGE.c.used_bytes.desc(), USER_USAGE.c.user_id)
        .limit(top)
    ).all()
    return AdminStorage(
        **summary.model_dump(),
        top_users=[
            AdminStorageUser(
                user_id=row.user_id,
                username=row.username,
                email=row.email,
                bytes=int(row.used_bytes),
                objects=int(row.object_count),
            )
            for row in rows
        ],
    )


@router.post(
    "/storage/reconcile",
    response_model=StorageSummary,
    responses={409: {"description": "Another reconcile is running"}, 502: {"description": "Reconcile failed"}},
)
def reconcile(admin: User = Depends(require_admin)) -> StorageSummary:
    """Recompute the storage counters from a bucket listing now (rate limited as an expensive route)."""
    logger.info("Storage reconcile requested by admin %s", admin.id)
    try:
        return reconcile_storage()
    except ReconcileInProgressError:
        raise ApiError(409, "A storage reconcile is already running", "RECONCILE_IN_PROGRESS") from None
    except Exception:
        logger.exception("Storage reconcile requested by admin %s failed", admin.id)
        raise ApiError(502, "Storage reconcile failed", "RECONCILE_FAILED") from None


# --- system ----------------------------------------------------------------------------------------------------------


@router.get("/system", response_model=AdminSystem)
async def system() -> AdminSystem:
    checks = {name: check_status for name, check_status, _ in await dependency_checks()}
    return AdminSystem(
        build=current_build_info(),
        environment=settings.ENVIRONMENT,
        checks=AdminSystemChecks(**checks),
        limits=AdminSystemLimits(
            storage_hard_limit_bytes=settings.STORAGE_HARD_LIMIT_BYTES,
            storage_user_quota_bytes=settings.STORAGE_USER_QUOTA_BYTES,
            max_upload_bytes=settings.MAX_UPLOAD_BYTES,
            max_json_body_bytes=settings.MAX_JSON_BODY_BYTES,
            rate_limit_default=settings.RATE_LIMIT_DEFAULT,
            rate_limit_expensive=settings.RATE_LIMIT_EXPENSIVE,
            rate_limit_anon=settings.RATE_LIMIT_ANON,
            rate_limit_enabled=settings.RATE_LIMIT_ENABLED,
        ),
    )


__all__ = ["router"]
