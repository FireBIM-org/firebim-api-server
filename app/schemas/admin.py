"""Response and request models of the admin API (app/api/admin.py)."""

from datetime import datetime

from pydantic import BaseModel

from app.api.build_info import BuildInfo
from app.schemas.storage import StoragePrefixUsage, StorageSummary


class AdminUserCounts(BaseModel):
    total: int
    active: int
    # created in the last 7 / 30 days
    new_7d: int
    new_30d: int
    # seen by the API in the last 7 days (user_activity.last_seen_at)
    active_7d: int


class AdminContentCounts(BaseModel):
    ifc_models: int
    ids_files: int
    models_pending: int
    models_processing: int
    models_processed: int


class AdminOverview(BaseModel):
    users: AdminUserCounts
    content: AdminContentCounts
    storage: StorageSummary
    generated_at: datetime


class AdminUser(BaseModel):
    id: str
    username: str
    email: str
    is_active: bool
    # The status this API process last determined when the user called it; None until then.
    is_admin: bool | None
    created_at: datetime
    last_seen_at: datetime | None
    ifc_models: int
    ids_files: int
    storage_bytes: int
    storage_objects: int


class AdminUserList(BaseModel):
    items: list[AdminUser]
    total: int


class AdminModelSummary(BaseModel):
    id: str
    filename: str
    processed: bool
    processing_status: str
    created_at: datetime


class AdminIdsFileSummary(BaseModel):
    id: str
    filename: str
    created_at: datetime


class AdminUserDetail(AdminUser):
    recent_models: list[AdminModelSummary]
    recent_ids_files: list[AdminIdsFileSummary]
    # Live listing of the user's objects per top-level prefix. Ontology sync jobs are not included, and the listing
    # stops early for users with very many uploads (storage_by_prefix_complete is then false).
    storage_by_prefix: list[StoragePrefixUsage]
    storage_by_prefix_complete: bool


class AdminUserUpdate(BaseModel):
    is_active: bool


class AdminStorageUser(BaseModel):
    user_id: str
    # None when the user no longer exists locally
    username: str | None
    email: str | None
    bytes: int
    objects: int


class AdminStorage(StorageSummary):
    top_users: list[AdminStorageUser]


class AdminSystemChecks(BaseModel):
    # "ok", "error" or (celery) "no workers responding"
    database: str
    s3: str
    celery: str
    authentik: str


class AdminSystemLimits(BaseModel):
    storage_hard_limit_bytes: int
    storage_user_quota_bytes: int
    max_upload_bytes: int
    max_json_body_bytes: int
    rate_limit_default: str
    rate_limit_expensive: str
    rate_limit_anon: str
    rate_limit_enabled: bool


class AdminSystem(BaseModel):
    build: BuildInfo
    environment: str
    checks: AdminSystemChecks
    limits: AdminSystemLimits


__all__ = [
    "AdminContentCounts",
    "AdminIdsFileSummary",
    "AdminModelSummary",
    "AdminOverview",
    "AdminStorage",
    "AdminStorageUser",
    "AdminSystem",
    "AdminSystemChecks",
    "AdminSystemLimits",
    "AdminUser",
    "AdminUserCounts",
    "AdminUserDetail",
    "AdminUserList",
    "AdminUserUpdate",
]
