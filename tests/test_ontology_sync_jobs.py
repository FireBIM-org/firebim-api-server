from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from app.ontologies import sync_task
from app.ontologies.sync_jobs import OntologySyncJobNotFoundError, OntologySyncJobStore
from app.schemas.ontologies import OntologySyncJobStatus, OntologySyncRequest


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(self.objects[Key])}


def test_sync_job_store_round_trip():
    client = FakeS3Client()
    store = OntologySyncJobStore(client=client, bucket_name="firebim")
    request = OntologySyncRequest(
        source_url="https://example.org/index.json",
        selections=["ifc/global/2026.04.23"],
    )

    created = store.create(owner_id="user-1", request=request)
    assert created["status"] == OntologySyncJobStatus.queued.value
    assert store.get(created["job_id"])["request"]["selections"] == ["ifc/global/2026.04.23"]

    updated = store.update(
        created["job_id"],
        status=OntologySyncJobStatus.completed.value,
        result={"published": 1},
    )
    assert updated["status"] == OntologySyncJobStatus.completed.value
    assert json.loads(next(iter(client.objects.values())))["result"] == {"published": 1}


def test_sync_job_store_rejects_unknown_ids():
    store = OntologySyncJobStore(client=FakeS3Client(), bucket_name="firebim")
    with pytest.raises(OntologySyncJobNotFoundError):
        store.get("not-a-uuid")


def test_worker_persists_completed_sync_result(monkeypatch):
    updates: list[dict] = []
    fake_store = SimpleNamespace(update=lambda _job_id, **changes: updates.append(changes))
    fake_result = SimpleNamespace(model_dump=lambda **_kwargs: {"published": 1, "failed": 0})

    monkeypatch.setattr(sync_task, "OntologySyncJobStore", lambda: fake_store)
    monkeypatch.setattr(sync_task, "OntologyRepository", lambda: object())
    monkeypatch.setattr(sync_task, "sync_from_registry", lambda _repo, _request: fake_result)

    response = sync_task.sync_ontologies_from_registry.run(
        "2a61b84d-3021-47bd-a5e2-bfe5ad9d052c",
        {"source_url": "https://example.org/index.json"},
    )

    assert updates[0]["status"] == OntologySyncJobStatus.running.value
    assert updates[1]["status"] == OntologySyncJobStatus.completed.value
    assert updates[1]["result"]["published"] == 1
    assert response["status"] == OntologySyncJobStatus.completed.value
