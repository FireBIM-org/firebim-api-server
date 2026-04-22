from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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

from app.api import ontologies as ontologies_api
from app.schemas.ontologies import (
    OntologyDeleteResponse,
    OntologyPublishResponse,
    OntologySummaryResponse,
    OntologyVersionListResponse,
    OntologyVersionResponse,
)


class DummyOntologyRepo:
    ROOT_PREFIX = "ontologies"

    def __init__(self):
        self.published_payload = None
        self.deleted_key = None

    def health(self):
        return True

    def get_summary(self, ontology_family: str, country: str):
        assert ontology_family == "fbo"
        assert country == "dk"
        return OntologySummaryResponse(
            ontology_family="fbo",
            country="dk",
            canonical_family_uri="https://firebim.org/ontology/fbo#",
            canonical_country_uri="https://firebim.org/ontology/fbo/dk#",
            latest_version="2026.04",
            latest_stable_version="2026.03",
            default_version="2026.03",
            available_versions_url="/ontologies/fbo/dk/versions",
            latest_url="/ontologies/fbo/dk/versions/latest",
            stable_url="/ontologies/fbo/dk/versions/stable",
        )

    def list_versions(self, ontology_family: str, country: str):
        assert ontology_family == "fbo"
        assert country == "dk"
        return OntologyVersionListResponse(
            ontology_family="fbo",
            country="dk",
            versions=[
                {
                    "version": "2026.04",
                    "status": "draft",
                    "issued_at": "2026-04-10",
                    "is_latest": True,
                    "is_stable": False,
                    "is_default": False,
                    "url": "/ontologies/fbo/dk/versions/2026.04",
                    "content_url": "/ontologies/fbo/dk/versions/2026.04/content",
                    "content_type": "text/turtle; charset=utf-8",
                },
                {
                    "version": "2026.03",
                    "status": "stable",
                    "issued_at": "2026-03-01",
                    "is_latest": False,
                    "is_stable": True,
                    "is_default": True,
                    "url": "/ontologies/fbo/dk/versions/2026.03",
                    "content_url": "/ontologies/fbo/dk/versions/2026.03/content",
                    "content_type": "text/turtle; charset=utf-8",
                },
            ],
        )

    def get_latest_version(self, ontology_family: str, country: str):
        return self.get_version(ontology_family, country, "2026.04")

    def get_latest_stable_version(self, ontology_family: str, country: str):
        return self.get_version(ontology_family, country, "2026.03")

    def get_version(self, ontology_family: str, country: str, version: str):
        assert ontology_family == "fbo"
        assert country == "dk"
        if version == "2026.04":
            return OntologyVersionResponse(
                ontology_family="fbo",
                country="dk",
                canonical_family_uri="https://firebim.org/ontology/fbo#",
                canonical_country_uri="https://firebim.org/ontology/fbo/dk#",
                version="2026.04",
                status="draft",
                issued_at="2026-04-10",
                is_latest=True,
                is_stable=False,
                is_default=False,
                url="/ontologies/fbo/dk/versions/2026.04",
                content_url="/ontologies/fbo/dk/versions/2026.04/content",
                content_type="text/turtle; charset=utf-8",
            )
        return OntologyVersionResponse(
            ontology_family="fbo",
            country="dk",
            canonical_family_uri="https://firebim.org/ontology/fbo#",
            canonical_country_uri="https://firebim.org/ontology/fbo/dk#",
            version="2026.03",
            status="stable",
            issued_at="2026-03-01",
            is_latest=False,
            is_stable=True,
            is_default=True,
            url="/ontologies/fbo/dk/versions/2026.03",
            content_url="/ontologies/fbo/dk/versions/2026.03/content",
            content_type="text/turtle; charset=utf-8",
        )

    def get_version_content(self, ontology_family: str, country: str, version: str):
        assert ontology_family == "fbo"
        assert country == "dk"
        assert version == "2026.03"
        return (b"@prefix fbo: <https://example.org/fbo/> .\n", "text/turtle; charset=utf-8")

    def publish_version(self, payload):
        self.published_payload = payload
        return OntologyPublishResponse(
            ontology_family="fbo",
            country="dk",
            canonical_family_uri="https://firebim.org/ontology/fbo#",
            canonical_country_uri="https://firebim.org/ontology/fbo/dk#",
            version=payload.version,
            status=payload.status,
            issued_at=payload.issued_at,
            is_latest=True,
            is_stable=payload.status == "stable",
            is_default=payload.default_version,
            url=f"/ontologies/fbo/dk/versions/{payload.version}",
            content_url=f"/ontologies/fbo/dk/versions/{payload.version}/content",
            content_type=payload.content_type,
            size_bytes=len(payload.ttl.encode("utf-8")),
            checksum_sha256="dummy-checksum",
            created=True,
            manifest_updated=True,
        )

    def delete_version(self, ontology_family: str, country: str, version: str):
        self.deleted_key = (ontology_family, country, version)
        return OntologyDeleteResponse(
            ontology_family=ontology_family,
            country=country,
            version=version,
            deleted=True,
            deleted_content=True,
            manifest_updated=True,
            remaining_versions=1,
        )


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ontologies_api.router)
    dummy_repo = DummyOntologyRepo()
    monkeypatch.setattr(ontologies_api, "repo", dummy_repo)
    app.dependency_overrides[ontologies_api.get_current_active_user] = lambda: object()
    return TestClient(app)


def test_ontology_health(client: TestClient):
    response = client.get("/ontologies/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["storage_connected"] is True
    assert body["storage_backend"] == "s3"


def test_ontology_summary(client: TestClient):
    response = client.get("/ontologies/fbo/dk")
    assert response.status_code == 200
    assert response.json() == {
        "ontology_family": "fbo",
        "country": "dk",
        "canonical_family_uri": "https://firebim.org/ontology/fbo#",
        "canonical_country_uri": "https://firebim.org/ontology/fbo/dk#",
        "latest_version": "2026.04",
        "latest_stable_version": "2026.03",
        "default_version": "2026.03",
        "available_versions_url": "/ontologies/fbo/dk/versions",
        "latest_url": "/ontologies/fbo/dk/versions/latest",
        "stable_url": "/ontologies/fbo/dk/versions/stable",
    }


def test_ontology_versions(client: TestClient):
    response = client.get("/ontologies/fbo/dk/versions")
    assert response.status_code == 200
    body = response.json()
    assert body["ontology_family"] == "fbo"
    assert body["country"] == "dk"
    assert [item["version"] for item in body["versions"]] == ["2026.04", "2026.03"]
    assert body["versions"][1]["is_stable"] is True
    assert body["versions"][1]["is_default"] is True


def test_ontology_latest_and_stable(client: TestClient):
    latest_response = client.get("/ontologies/fbo/dk/versions/latest")
    stable_response = client.get("/ontologies/fbo/dk/versions/stable")

    assert latest_response.status_code == 200
    assert latest_response.json()["version"] == "2026.04"
    assert latest_response.json()["is_latest"] is True

    assert stable_response.status_code == 200
    assert stable_response.json()["version"] == "2026.03"
    assert stable_response.json()["is_stable"] is True


def test_ontology_content(client: TestClient):
    response = client.get("/ontologies/fbo/dk/versions/2026.03/content")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/turtle")
    assert "@prefix fbo:" in response.text


def test_legacy_api_prefix_still_works(client: TestClient):
    response = client.get("/api/ontologies/fbo/dk")
    assert response.status_code == 200
    assert response.json()["canonical_country_uri"] == "https://firebim.org/ontology/fbo/dk#"


def test_publish_ontology_example(client: TestClient):
    response = client.post(
        "/ontologies/publish",
        json={
            "ontology_family": "fbo",
            "country": "dk",
            "version": "2026.05",
            "status": "candidate",
            "issued_at": "2026-05-15",
            "ttl": (
                "@prefix fbo: <https://firebim.org/ontology/fbo/dk#> .\n"
                "<https://firebim.org/ontology/fbo/dk#Wall> a "
                "<https://www.w3.org/2002/07/owl#Class> .\n"
            ),
            "default_version": False,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["version"] == "2026.05"
    assert body["status"] == "candidate"
    assert body["url"] == "/ontologies/fbo/dk/versions/2026.05"
    assert body["created"] is True


def test_delete_ontology_version(client: TestClient):
    response = client.delete("/ontologies/fbo/dk/versions/2026.03")
    assert response.status_code == 200
    body = response.json()
    assert body["ontology_family"] == "fbo"
    assert body["country"] == "dk"
    assert body["version"] == "2026.03"
    assert body["deleted"] is True
