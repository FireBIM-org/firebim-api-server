import runpy
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError

from app.core.config import settings
from app.storage.ingestion_objects import IngestionObjects


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:3000,https://app.example.org",
        '["http://localhost:3000", "https://app.example.org"]',
    ],
)
def test_cors_environment_accepts_compose_and_json_lists(monkeypatch, value):
    from app.core.config import Settings

    monkeypatch.setenv("S3_BROWSER_CORS_ORIGINS", value)
    monkeypatch.delenv("NEO_INGESTION_ENABLED", raising=False)
    monkeypatch.delenv("NEO_INGESTION_MAX_INPUT_BYTES", raising=False)
    configured = Settings(_env_file=None)
    assert [str(origin).rstrip("/") for origin in configured.S3_BROWSER_CORS_ORIGINS] == [
        "http://localhost:3000",
        "https://app.example.org",
    ]
    assert not configured.NEO_INGESTION_ENABLED
    assert configured.NEO_INGESTION_MAX_INPUT_BYTES == 0


def load_s3(monkeypatch, public_endpoint=None):
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setitem(settings.__dict__, "S3_PUBLIC_ENDPOINT_URL", public_endpoint)
    return runpy.run_path(str(Path(__file__).parents[1] / "app/core/s3.py"))


@pytest.mark.parametrize("public_endpoint", ["http://localhost:9000", "https://uploads.example.org"])
def test_upload_is_signed_for_browser_endpoint_not_internal_host(monkeypatch, public_endpoint):
    module = load_s3(monkeypatch, public_endpoint)
    signer = module.get("s3_upload_client", module["s3_client"])
    url = signer.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": "private",
            "Key": "ingestion/staging/test.ifc",
            "ContentType": "application/octet-stream",
            "ContentLength": 42,
        },
        ExpiresIn=60,
        HttpMethod="PUT",
    )
    assert urlsplit(url).netloc == urlsplit(public_endpoint).netloc
    assert urlsplit(url).path == "/private/ingestion/staging/test.ifc"
    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-SignedHeaders"] == ["content-length;content-type;host"]
    assert module["s3_client"].meta.endpoint_url == "http://minio:9000"


def test_public_endpoint_is_optional_for_already_public_storage(monkeypatch):
    module = load_s3(monkeypatch)
    assert module["s3_upload_client"].meta.endpoint_url == "http://minio:9000"


def test_only_presigning_uses_public_client():
    internal, signer = Mock(), Mock()
    objects = IngestionObjects(internal, "private", signing_client=signer)
    objects.upload_url("ingestion/staging/test.ifc", 42, 60)
    signer.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={
            "Bucket": "private",
            "Key": "ingestion/staging/test.ifc",
            "ContentType": "application/octet-stream",
            "ContentLength": 42,
        },
        ExpiresIn=60,
        HttpMethod="PUT",
    )
    assert objects.client is internal
    internal.generate_presigned_url.assert_not_called()


def test_admission_dependency_uses_separate_signing_client(session, monkeypatch):
    from app.api import models

    signer = Mock()
    monkeypatch.setattr(models, "s3_upload_client", signer, raising=False)
    service = models.get_ingestion_admission(session)
    service.objects.upload_url("ingestion/staging/test.ifc", 42, 60)
    signer.generate_presigned_url.assert_called_once()


def test_browser_cors_preserves_unrelated_rules_and_updates_owned_rule(monkeypatch):
    module = load_s3(monkeypatch)
    unrelated = {"ID": "downloads", "AllowedOrigins": ["https://other.example.org"], "AllowedMethods": ["GET"]}
    client = Mock()
    client.get_bucket_cors.return_value = {"CORSRules": [unrelated, {"ID": "firebim-direct-upload"}]}
    module["configure_browser_upload_cors"](client, "private", ["http://localhost:3000"])
    rules = client.put_bucket_cors.call_args.kwargs["CORSConfiguration"]["CORSRules"]
    assert rules == [
        unrelated,
        {
            "ID": "firebim-direct-upload",
            "AllowedOrigins": ["http://localhost:3000"],
            "AllowedMethods": ["PUT"],
            "AllowedHeaders": ["content-type"],
            "MaxAgeSeconds": 600,
        },
    ]
    assert client.put_bucket_cors.call_args.kwargs["Bucket"] == "private"


def test_browser_cors_is_opt_in_and_does_not_require_bucket_admin_by_default(monkeypatch):
    module = load_s3(monkeypatch)
    client = Mock()
    module["configure_browser_upload_cors"](client, "private", [])
    assert not client.mock_calls


@pytest.mark.parametrize("code", ["NoSuchCORSConfiguration", "AccessDenied"])
def test_browser_cors_only_treats_missing_configuration_as_empty(monkeypatch, code):
    module = load_s3(monkeypatch)
    client = Mock()
    client.get_bucket_cors.side_effect = ClientError({"Error": {"Code": code}}, "GetBucketCors")
    if code == "AccessDenied":
        with pytest.raises(ClientError):
            module["configure_browser_upload_cors"](client, "private", ["http://localhost:3000"])
        client.put_bucket_cors.assert_not_called()
    else:
        module["configure_browser_upload_cors"](client, "private", ["http://localhost:3000"])
        assert len(client.put_bucket_cors.call_args.kwargs["CORSConfiguration"]["CORSRules"]) == 1
