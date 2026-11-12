from datetime import datetime

from pydantic import BaseModel


class StoragePrefixUsage(BaseModel):
    prefix: str
    bytes: int
    objects: int


class StorageSummary(BaseModel):
    """Bucket usage as tracked by the storage accounting tables (see app/storage/quota.py)."""

    used_bytes: int
    object_count: int
    limit_bytes: int
    user_quota_bytes: int
    # used_bytes / limit_bytes; 0.0 when the hard limit is disabled
    usage_ratio: float
    warn_ratio: float
    last_reconciled_at: datetime | None = None
    by_prefix: list[StoragePrefixUsage] = []


class StorageUserUsage(BaseModel):
    user_id: str
    bytes: int
    objects: int
