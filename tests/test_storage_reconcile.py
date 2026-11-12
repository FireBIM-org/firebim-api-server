from __future__ import annotations

import json
from io import BytesIO

import pytest
from botocore.exceptions import ClientError
from sqlmodel import Session

from app.core import s3 as core_s3
from app.core.config import settings
from app.models.models import IdsFile, IfcModel
from app.storage import quota, reconcile

JOB_KEY = "ontology-sync/jobs/2a61b84d-3021-47bd-a5e2-bfe5ad9d052c.json"


class PagedS3Client:
    """A bucket listing that is split into pages of ``page_size`` keys, like list_objects_v2."""

    def __init__(self, objects: dict[str, bytes], page_size: int = 3) -> None:
        self.objects = objects
        self.page_size = page_size
        self.list_calls = 0

    def list_objects_v2(self, *, Bucket, MaxKeys=1000, ContinuationToken=None):
        self.list_calls += 1
        keys = sorted(self.objects)
        start = int(ContinuationToken or 0)
        page = keys[start : start + min(MaxKeys, self.page_size)]
        end = start + len(page)
        response = {
            "Contents": [{"Key": key, "Size": len(self.objects[key])} for key in page],
            "IsTruncated": end < len(keys),
        }
        if end < len(keys):
            response["NextContinuationToken"] = str(end)
        return response

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(self.objects[Key])}


@pytest.fixture()
def bucket(session: Session) -> PagedS3Client:
    session.add(IfcModel(id="m1", filename="a.ifc", s3_key="models/m1/a.ifc", user_id="u1"))
    session.add(IdsFile(id="i1", filename="x.ids", s3_key="ids/i1/x.ids", user_id="u2"))
    session.commit()
    return PagedS3Client(
        {
            "models/m1/a.ifc": b"m" * 100,  # u1 via ifc_models.s3_key
            "models/m2/b.ifc": b"m" * 50,  # no row: global
            "results/m1/stats.json": b"r" * 10,  # u1 via the model id
            "ids/i1/x.ids": b"i" * 20,  # u2 via ids_files.s3_key
            "lexicon/mappings/u2/doc.json": b"l" * 5,  # u2 via the key layout
            "rulebuilder/rule-element-groups/u1%40x/g.json": b"g" * 7,  # "u1@x"
            JOB_KEY: json.dumps({"job_id": "j", "owner_id": "u3"}).encode(),  # u3 via the job document
            "graphs/buildings/x.ttl": b"t" * 1000,
            "checks/c1/result.json": b"c" * 3,
            "misc/readme.txt": b"o",
        }
    )


def _by_prefix(summary) -> dict[str, tuple[int, int]]:
    return {item.prefix: (item.bytes, item.objects) for item in summary.by_prefix}


def test_reconcile_recomputes_absolute_totals(bucket: PagedS3Client):
    # stale counters, including a user that no longer owns anything
    quota.reserve(5_000, prefix="models", user_id="ghost")
    job_size = len(bucket.objects[JOB_KEY])

    summary = reconcile.reconcile_storage(s3_client=bucket, bucket="bucket")

    total = sum(len(body) for body in bucket.objects.values())
    assert bucket.list_calls == 4  # 10 keys in pages of 3
    assert (summary.used_bytes, summary.object_count) == (total, 10)
    assert summary.last_reconciled_at is not None
    prefixes = _by_prefix(summary)
    assert prefixes["models"] == (150, 2)
    assert prefixes["graphs"] == (1000, 1)
    assert prefixes["results"] == (10, 1)
    assert prefixes["ids"] == (20, 1)
    assert prefixes["lexicon"] == (5, 1)
    assert prefixes["rulebuilder"] == (7, 1)
    assert prefixes["ontology-sync"] == (job_size, 1)
    assert prefixes["checks"] == (3, 1)
    assert prefixes["other"] == (1, 1)
    assert prefixes["ontologies"] == (0, 0)
    users = {usage.user_id: (usage.bytes, usage.objects) for usage in quota.get_top_users(limit=100)}
    assert users == {"u1": (110, 2), "u2": (25, 2), "u1@x": (7, 1), "u3": (job_size, 1)}


def test_reconcile_counts_continue_from_the_reconciled_values(bucket: PagedS3Client):
    reconcile.reconcile_storage(s3_client=bucket, bucket="bucket")
    before = quota.get_storage_summary().used_bytes

    quota.reserve(30, prefix="models", user_id="u1")

    assert quota.get_storage_summary().used_bytes == before + 30
    assert quota.get_user_usage("u1").bytes == 140


def test_reconcile_only_if_never_reconciled_runs_once(bucket: PagedS3Client):
    assert reconcile.reconcile_storage(s3_client=bucket, bucket="bucket", only_if_never_reconciled=True) is not None
    calls = bucket.list_calls

    assert reconcile.reconcile_storage(s3_client=bucket, bucket="bucket", only_if_never_reconciled=True) is None
    assert bucket.list_calls == calls


def test_reconcile_of_an_empty_bucket(session: Session):
    summary = reconcile.reconcile_storage(s3_client=PagedS3Client({}), bucket="bucket")

    assert (summary.used_bytes, summary.object_count) == (0, 0)
    assert summary.last_reconciled_at is not None
    assert quota.get_top_users() == []


def test_unreadable_sync_job_counts_as_global(session: Session):
    client = PagedS3Client({JOB_KEY: b"not json"})

    summary = reconcile.reconcile_storage(s3_client=client, bucket="bucket")

    assert summary.used_bytes == len(b"not json")
    assert quota.get_top_users() == []


def test_truncated_listing_without_token_fails():
    class BrokenClient:
        def list_objects_v2(self, **_kwargs):
            return {"Contents": [{"Key": "a", "Size": 1}], "IsTruncated": True}

    with pytest.raises(RuntimeError):
        list(reconcile.list_bucket_objects(BrokenClient(), "bucket"))


def test_cli_prints_the_summary_and_exits_zero(bucket: PagedS3Client, monkeypatch, capsys):
    monkeypatch.setattr(core_s3, "s3_client", bucket)
    monkeypatch.setattr(settings, "STORAGE_HARD_LIMIT_BYTES", 10_000)

    assert reconcile.main() == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["object_count"] == 10
    assert printed["limit_bytes"] == 10_000
    assert printed["last_reconciled_at"] is not None
    assert {item["prefix"] for item in printed["by_prefix"]} >= {"models", "graphs", "other"}


def test_cli_exits_non_zero_on_failure(session: Session, monkeypatch, capsys):
    class FailingClient:
        def list_objects_v2(self, **_kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectsV2")

    monkeypatch.setattr(core_s3, "s3_client", FailingClient())

    assert reconcile.main() == 1
    assert capsys.readouterr().out == ""
    assert quota.get_storage_summary().last_reconciled_at is None


def test_cli_exits_two_when_another_reconcile_runs(monkeypatch):
    def busy(**_kwargs):
        raise reconcile.ReconcileInProgressError("busy")

    monkeypatch.setattr(reconcile, "reconcile_storage", busy)

    assert reconcile.main() == 2


def test_startup_reconcile_runs_in_the_background_once(bucket: PagedS3Client, monkeypatch):
    monkeypatch.setattr(core_s3, "s3_client", bucket)

    reconcile.start_startup_reconcile().join(timeout=30)
    assert quota.get_storage_summary().last_reconciled_at is not None
    calls = bucket.list_calls

    reconcile.start_startup_reconcile().join(timeout=30)
    assert bucket.list_calls == calls


def test_startup_reconcile_never_raises(monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("S3 down")

    monkeypatch.setattr(reconcile, "reconcile_storage", boom)

    reconcile.start_startup_reconcile().join(timeout=30)
