from __future__ import annotations

import os
from io import BytesIO
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from pydantic import BaseModel

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_DB", "firebim")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_BUCKET_NAME", "firebim")
os.environ.setdefault("S3_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("S3_SECRET_ACCESS_KEY", "minioadmin")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

from app.storage.s3_json_store import (
    S3JsonStoreError,
    S3JsonStoreNotFoundError,
    UserScopedS3JsonStore,
)


class ExampleDocument(BaseModel):
    id: str
    name: str


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def get_object(self, *, Bucket: str, Key: str):
        try:
            payload = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise _client_error("NoSuchKey") from exc
        return {"Body": BytesIO(payload)}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str):
        _ = ContentType
        self.objects[(Bucket, Key)] = Body

    def list_objects_v2(self, *, Bucket: str, Prefix: str, MaxKeys: int, ContinuationToken=None):
        _ = MaxKeys, ContinuationToken
        contents = [
            {"Key": key}
            for (bucket, key), _payload in sorted(self.objects.items())
            if bucket == Bucket and key.startswith(Prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def head_object(self, *, Bucket: str, Key: str):
        if (Bucket, Key) not in self.objects:
            raise _client_error("NoSuchKey")
        return {}

    def delete_object(self, *, Bucket: str, Key: str):
        self.objects.pop((Bucket, Key), None)


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "s3")


def test_write_read_and_list_model_documents():
    store = UserScopedS3JsonStore(bucket_name="firebim", s3_client=FakeS3Client())
    user = SimpleNamespace(id="user-1")
    payload = ExampleDocument(id="doc-1", name="Fire compartment")

    store.write_model(
        "lexicon/mappings",
        user,
        payload.id,
        payload,
        write_error_message="write failed",
    )

    loaded = store.read_model(
        "lexicon/mappings",
        user,
        payload.id,
        model_type=ExampleDocument,
        not_found_message="not found",
        fetch_error_message="fetch failed",
        payload_error_message="payload invalid",
    )

    assert loaded == payload
    assert store.list_document_ids("lexicon/mappings", user) == ["doc-1"]


def test_delete_document_removes_object():
    store = UserScopedS3JsonStore(bucket_name="firebim", s3_client=FakeS3Client())
    user = SimpleNamespace(id="user-1")

    store.write_dict(
        "ontologies/candidates/dictionaries",
        user,
        "cand-1",
        {"name": "candidate"},
        write_error_message="write failed",
    )

    store.delete_document(
        "ontologies/candidates/dictionaries",
        user,
        "cand-1",
        not_found_message="candidate missing",
        delete_error_message="delete failed",
    )

    assert store.list_document_ids("ontologies/candidates/dictionaries", user) == []


def test_read_model_raises_not_found_for_missing_document():
    store = UserScopedS3JsonStore(bucket_name="firebim", s3_client=FakeS3Client())
    user = SimpleNamespace(id="user-1")

    with pytest.raises(S3JsonStoreNotFoundError, match="missing"):
        store.read_model(
            "lexicon/handshakes",
            user,
            "hs-1",
            model_type=ExampleDocument,
            not_found_message="missing",
            fetch_error_message="fetch failed",
            payload_error_message="payload invalid",
        )


def test_read_model_raises_payload_error_for_invalid_json():
    store = UserScopedS3JsonStore(bucket_name="firebim", s3_client=FakeS3Client())
    user = SimpleNamespace(id="user-1")
    store.write_json_text(
        "lexicon/handshakes",
        user,
        "hs-1",
        '{"id":"hs-1"}',
        write_error_message="write failed",
    )

    with pytest.raises(S3JsonStoreError, match="payload invalid"):
        store.read_model(
            "lexicon/handshakes",
            user,
            "hs-1",
            model_type=ExampleDocument,
            not_found_message="missing",
            fetch_error_message="fetch failed",
            payload_error_message="payload invalid",
        )
