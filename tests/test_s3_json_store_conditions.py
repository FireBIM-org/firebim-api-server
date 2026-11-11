from io import BytesIO
from types import SimpleNamespace

import boto3
import pytest
from botocore.stub import Stubber
from pydantic import BaseModel

from app.storage.s3_json_store import (
    S3JsonStoreConflictError,
    S3JsonStoreError,
    UserScopedS3JsonStore,
)


class Document(BaseModel):
    name: str


@pytest.fixture
def storage():
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
    store = UserScopedS3JsonStore(bucket_name="test", s3_client=client)
    with Stubber(client) as stubber:
        yield store, stubber
        stubber.assert_no_pending_responses()


def test_read_returns_payload_and_etag_from_the_same_response(storage):
    store, stubber = storage
    stubber.add_response(
        "get_object",
        {"Body": BytesIO(b'{"name":"draft"}'), "ETag": '"etag-1"'},
        {"Bucket": "test", "Key": "drafts/user/draft.json"},
    )
    document, etag = store.read_model_with_etag(
        "drafts",
        SimpleNamespace(id="user"),
        "draft",
        model_type=Document,
        not_found_message="missing",
        fetch_error_message="fetch failed",
        payload_error_message="invalid",
    )
    assert document.name == "draft"
    assert etag == '"etag-1"'


@pytest.mark.parametrize("etag", [None, '"etag-1"'])
def test_conditional_writes_send_server_side_precondition(storage, etag):
    store, stubber = storage
    document = Document(name="draft")
    stubber.add_response(
        "put_object",
        {},
        {
            "Bucket": "test",
            "Key": "drafts/user/draft.json",
            "Body": document.model_dump_json(indent=2).encode(),
            "ContentType": "application/json",
            **({"IfNoneMatch": "*"} if etag is None else {"IfMatch": etag}),
        },
    )
    store.write_model_conditionally(
        "drafts",
        SimpleNamespace(id="user"),
        "draft",
        document,
        expected_etag=etag,
        write_error_message="write failed",
    )


@pytest.mark.parametrize("code", ["PreconditionFailed", "ConditionalRequestConflict", "412", "409"])
def test_precondition_conflicts_are_distinct_from_storage_failure(storage, code):
    store, stubber = storage
    stubber.add_client_error("put_object", service_error_code=code, http_status_code=412)
    with pytest.raises(S3JsonStoreConflictError):
        store.write_model_conditionally(
            "drafts",
            SimpleNamespace(id="user"),
            "draft",
            Document(name="draft"),
            expected_etag='"old"',
            write_error_message="write failed",
        )


def test_backend_without_conditional_write_support_fails_closed(storage):
    store, stubber = storage
    stubber.add_client_error("put_object", service_error_code="NotImplemented", http_status_code=501)
    with pytest.raises(S3JsonStoreError, match="write failed"):
        store.write_model_conditionally(
            "drafts",
            SimpleNamespace(id="user"),
            "draft",
            Document(name="draft"),
            expected_etag='"old"',
            write_error_message="write failed",
        )


def test_missing_etag_is_not_treated_as_a_create(storage):
    store, stubber = storage
    stubber.add_response("get_object", {"Body": BytesIO(b'{"name":"draft"}')})
    with pytest.raises(S3JsonStoreError, match="ETag"):
        store.read_model_with_etag(
            "drafts",
            SimpleNamespace(id="user"),
            "draft",
            model_type=Document,
            not_found_message="missing",
            fetch_error_message="fetch failed",
            payload_error_message="invalid",
        )
