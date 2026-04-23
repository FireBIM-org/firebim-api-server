import os
from datetime import UTC, date, datetime

# Minimal settings needed before importing the API modules.
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

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ontologies as ontologies_api
from app.schemas.ontologies import OntologyReleaseStatus, OntologyVersionFetchResponse, OntologyVersionListResponse


class DummyOntologyRepo:
    def health(self) -> bool:
        return True

    def resource_directories(self) -> list[str]:
        return ["C:\\dummy\\resources"]

    def get_summary(self, ontology_family: str, country: str) -> dict:
        return {
            "ontology_family": ontology_family,
            "country": country,
            "canonical_family_uri": f"https://firebim.org/ontologies/{ontology_family}#",
            "canonical_country_uri": f"https://firebim.org/ontologies/{ontology_family}/{country}#",
            "latest_version": "2026.04.23",
            "latest_stable_version": "2026.03",
            "default_version": "2026.04.23",
            "available_versions_url": f"/ontologies/{ontology_family}/{country}/versions",
            "latest_url": f"/ontologies/{ontology_family}/{country}/versions/latest",
            "stable_url": f"/ontologies/{ontology_family}/{country}/versions/stable",
        }

    def list_versions(self, ontology_family: str, country: str):
        return OntologyVersionListResponse(
            ontology_family=ontology_family,
            country=country,
            canonical_family_uri=f"https://firebim.org/ontologies/{ontology_family}#",
            canonical_country_uri=f"https://firebim.org/ontologies/{ontology_family}/{country}#",
            versions=[
                {
                    "version": "2026.04.23",
                    "status": OntologyReleaseStatus.draft,
                    "issued_at": date(2026, 4, 23),
                    "is_latest": True,
                    "is_stable": False,
                    "is_default": True,
                    "url": f"/ontologies/{ontology_family}/{country}/versions/2026.04.23",
                    "ttl_url": f"/ontologies/{ontology_family}/{country}/versions/2026.04.23/ttl",
                    "graph_uri": f"https://api.firebim.org/ontologies/{ontology_family}/{country}/versions/2026.04.23",
                    "ontology_uri": f"https://firebim.org/ontologies/{ontology_family}/{country}#",
                    "version_iri": f"https://firebim.org/ontologies/{ontology_family}/{country}/2026.04.23#",
                    "label": "FireBIM DK Ontology",
                    "description": "Draft ontology",
                    "triple_count": 12,
                    "byte_size": 345,
                    "sha256": "abc123",
                    "imports": [],
                    "resource_filename": "fbo-dk.ttl",
                    "published_at": datetime(2026, 4, 23, tzinfo=UTC),
                },
                {
                    "version": "2026.03",
                    "status": OntologyReleaseStatus.stable,
                    "issued_at": date(2026, 3, 1),
                    "is_latest": False,
                    "is_stable": True,
                    "is_default": False,
                    "url": f"/ontologies/{ontology_family}/{country}/versions/2026.03",
                    "ttl_url": f"/ontologies/{ontology_family}/{country}/versions/2026.03/ttl",
                    "graph_uri": f"https://api.firebim.org/ontologies/{ontology_family}/{country}/versions/2026.03",
                    "ontology_uri": f"https://firebim.org/ontologies/{ontology_family}/{country}#",
                    "version_iri": f"https://firebim.org/ontologies/{ontology_family}/{country}/2026.03#",
                    "label": "FireBIM DK Ontology",
                    "description": "Stable ontology",
                    "triple_count": 11,
                    "byte_size": 320,
                    "sha256": "def456",
                    "imports": [],
                    "resource_filename": "fbo-dk.ttl",
                    "published_at": datetime(2026, 4, 23, tzinfo=UTC),
                },
            ],
        )

    def fetch_version(self, ontology_family: str, country: str, version: str):
        resolved = "2026.04.23" if version == "latest" else "2026.03" if version == "stable" else version
        return OntologyVersionFetchResponse(
            ontology_family=ontology_family,
            country=country,
            canonical_family_uri=f"https://firebim.org/ontologies/{ontology_family}#",
            canonical_country_uri=f"https://firebim.org/ontologies/{ontology_family}/{country}#",
            version={
                "version": resolved,
                "status": OntologyReleaseStatus.stable if resolved == "2026.03" else OntologyReleaseStatus.draft,
                "issued_at": date(2026, 3, 1) if resolved == "2026.03" else date(2026, 4, 23),
                "is_latest": resolved == "2026.04.23",
                "is_stable": resolved == "2026.03",
                "is_default": resolved == "2026.04.23",
                "url": f"/ontologies/{ontology_family}/{country}/versions/{resolved}",
                "ttl_url": f"/ontologies/{ontology_family}/{country}/versions/{resolved}/ttl",
                "graph_uri": f"https://api.firebim.org/ontologies/{ontology_family}/{country}/versions/{resolved}",
                "ontology_uri": f"https://firebim.org/ontologies/{ontology_family}/{country}#",
                "version_iri": f"https://firebim.org/ontologies/{ontology_family}/{country}/{resolved}#",
                "label": "FireBIM DK Ontology",
                "description": "Ontology release",
                "triple_count": 12,
                "byte_size": 345,
                "sha256": "abc123",
                "imports": [],
                "resource_filename": "fbo-dk.ttl",
                "published_at": datetime(2026, 4, 23, tzinfo=UTC),
            },
            ttl=(
                "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
                "@prefix fbo-dk: <https://firebim.org/ontologies/fbo/dk#> .\n"
                "<https://firebim.org/ontologies/fbo/dk#> a owl:Ontology .\n"
            ),
        )

    def publish_version(self, payload):
        return {
            "published": True,
            "ontology_family": payload.ontology_family,
            "country": payload.country,
            "version": payload.version,
            "status": payload.status,
            "graph_uri": f"https://api.firebim.org/ontologies/{payload.ontology_family}/{payload.country}/versions/{payload.version}",
            "ontology_uri": f"https://firebim.org/ontologies/{payload.ontology_family}/{payload.country}#",
            "canonical_family_uri": f"https://firebim.org/ontologies/{payload.ontology_family}#",
            "canonical_country_uri": f"https://firebim.org/ontologies/{payload.ontology_family}/{payload.country}#",
            "default_version": payload.version,
            "overwritten": False,
            "triple_count": 12,
            "byte_size": 345,
            "resource_filename": payload.resource_filename,
        }

    def delete_version(self, ontology_family: str, country: str, version: str) -> str:
        return f"https://api.firebim.org/ontologies/{ontology_family}/{country}/versions/{version}"


def _dummy_current_user():
    return {"id": "user-1"}


def _build_client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(ontologies_api.router)
    monkeypatch.setattr(ontologies_api, "repo", DummyOntologyRepo())
    app.dependency_overrides[ontologies_api.get_current_active_user] = _dummy_current_user
    return TestClient(app)


def test_ontologies_health(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.get("/ontologies/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dataset"] == "ontologies"
    assert body["resource_directories"] == ["C:\\dummy\\resources"]


def test_ontology_summary_and_versions(monkeypatch):
    client = _build_client(monkeypatch)

    summary = client.get("/ontologies/fbo/dk")
    assert summary.status_code == 200
    assert summary.json()["default_version"] == "2026.04.23"

    versions = client.get("/ontologies/fbo/dk/versions")
    assert versions.status_code == 200
    body = versions.json()
    assert len(body["versions"]) == 2
    assert body["versions"][0]["version"] == "2026.04.23"
    assert body["versions"][0]["is_default"] is True


def test_fetch_latest_stable_and_ttl(monkeypatch):
    client = _build_client(monkeypatch)

    latest = client.get("/ontologies/fbo/dk/versions/latest")
    assert latest.status_code == 200
    assert latest.json()["version"]["version"] == "2026.04.23"

    stable = client.get("/ontologies/fbo/dk/versions/stable")
    assert stable.status_code == 200
    assert stable.json()["version"]["version"] == "2026.03"

    ttl = client.get("/ontologies/fbo/dk/versions/2026.03/ttl")
    assert ttl.status_code == 200
    assert "text/turtle" in ttl.headers["content-type"]
    assert "owl:Ontology" in ttl.text


def test_publish_and_delete_ontology(monkeypatch):
    client = _build_client(monkeypatch)

    publish = client.post(
        "/ontologies/publish",
        json={
            "ontology_family": "fbo",
            "country": "dk",
            "version": "2026.04.23",
            "status": "draft",
            "issued_at": "2026-04-23",
            "resource_filename": "fbo-dk.ttl",
            "default_version": True,
        },
    )
    assert publish.status_code == 200
    publish_body = publish.json()
    assert publish_body["published"] is True
    assert publish_body["resource_filename"] == "fbo-dk.ttl"

    delete = client.delete("/ontologies/fbo/dk/versions/2026.04.23")
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True
