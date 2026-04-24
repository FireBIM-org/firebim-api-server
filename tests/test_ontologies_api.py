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
from app.schemas.ontologies import (
    OntologyDictionaryCandidate,
    OntologyReleaseStatus,
    OntologyVersionFetchResponse,
    OntologyVersionListResponse,
)


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

    def load_local_resource(self, filename: str) -> str:
        if filename == "rir-1.0.json":
            return (
                "{"
                '"DictionaryName":"RIR",'
                '"DictionaryCode":"rir",'
                '"DictionaryVersion":"1.0",'
                '"Classes":[{"Code":"FireCompartment","Name":"Fire Compartment","ClassType":"Class"}],'
                '"Properties":[{"Code":"AreaFireCompartment","Name":"Area","DataType":"Real"}]'
                "}"
            )
        raise ValueError(f"Local ontology resource not found: {filename}")

    def save_local_resource(self, filename: str, content: str) -> str:
        assert filename.endswith(".ttl")
        assert "owl:Ontology" in content
        return f"C:\\dummy\\resources\\{filename}"


class DummyOntologyCandidateRepo:
    def __init__(self) -> None:
        self.deleted_id = None
        self.updated_reports = []
        self.candidate = OntologyDictionaryCandidate(
            id="cand_123",
            name="rir/global candidate",
            ontology_family="rir",
            country="global",
            version="2026.03.25",
            source_resource_filename="rir-1.0.json",
            source_dictionary_name="RIR",
            source_dictionary_code="rir",
            source_dictionary_version="1.0",
            dictionary_json={
                "DictionaryName": "RIR",
                "DictionaryCode": "rir",
                "DictionaryVersion": "1.0",
                "Classes": [
                    {
                        "Code": "FireCompartment",
                        "Name": "Fire Compartment",
                        "ClassType": "Class",
                        "Definition": "A fire compartment",
                        "Description": "A bounded fire compartment",
                        "RelatedIfcEntityNamesList": ["IfcSpace"],
                        "CountriesOfUse": ["DK"],
                        "Uid": "rir-fire-compartment",
                    }
                ],
                "Properties": [
                    {
                        "Code": "AreaFireCompartment",
                        "PropertyCode": "AreaFireCompartment",
                        "PropertyUri": "https://example.org/rir/area-fire-compartment",
                        "Definition": "Area definition",
                        "Description": "Fire compartment area",
                        "Unit": "m2",
                        "DataType": "Real",
                        "PropertyType": "SingleValue",
                        "PropertyValueKind": "Single",
                        "AllowedValues": [],
                        "Example": "12.5",
                    }
                ],
            },
            created_at="2026-04-24T08:00:00+00:00",
            updated_at="2026-04-24T08:00:00+00:00",
            last_validation_report=None,
        )

    def create_candidate(self, payload, *, dictionary_json, user):
        _ = payload, user
        self.candidate = self.candidate.model_copy(
            update={
                "dictionary_json": dictionary_json,
                "source_dictionary_name": dictionary_json.get("DictionaryName"),
                "source_dictionary_code": dictionary_json.get("DictionaryCode"),
                "source_dictionary_version": dictionary_json.get("DictionaryVersion"),
            }
        )
        return self.candidate

    def list_candidates(self, user):
        _ = user
        return [self.candidate]

    def get_candidate(self, candidate_id, user):
        _ = user
        return self.candidate.model_copy(update={"id": candidate_id})

    def update_validation_report(self, candidate_id, validation_report, user):
        _ = user
        self.updated_reports.append((candidate_id, validation_report))
        self.candidate = self.candidate.model_copy(
            update={
                "id": candidate_id,
                "last_validation_report": validation_report,
            }
        )
        return self.candidate

    def delete_candidate(self, candidate_id, user):
        _ = user
        self.deleted_id = candidate_id


def _dummy_current_user():
    return {"id": "user-1"}


def _build_client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(ontologies_api.router)
    monkeypatch.setattr(ontologies_api, "repo", DummyOntologyRepo())
    monkeypatch.setattr(ontologies_api, "candidate_repo", DummyOntologyCandidateRepo())
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


def test_convert_dictionary_json_to_ttl(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.post(
        "/ontologies/conversions/dictionary-json-to-ttl",
        json={
            "ontology_family": "rir",
            "country": "global",
            "version": "2026.03.25",
            "source_resource_filename": "rir-1.0.json",
            "output_resource_filename": "rir-global.ttl",
            "publish": True,
            "status": "candidate",
            "issued_at": "2026-03-25",
            "default_version": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_dictionary_code"] == "rir"
    assert body["class_count"] == 1
    assert body["property_count"] == 1
    assert body["subclass_count"] == 0
    assert body["published"] is True
    assert body["publish_result"]["graph_uri"] == "https://api.firebim.org/ontologies/rir/global/versions/2026.03.25"
    assert "owl:Ontology" in body["ttl"]
    assert body["validation_report"]["valid"] is False


def test_dictionary_candidate_crud_validate_convert_and_publish(monkeypatch):
    client = _build_client(monkeypatch)

    create = client.post(
        "/ontologies/candidates/dictionaries",
        json={
            "ontology_family": "rir",
            "country": "global",
            "version": "2026.03.25",
            "source_resource_filename": "rir-1.0.json",
            "name": "RIR candidate",
        },
    )
    assert create.status_code == 201
    assert create.json()["candidate"]["source_dictionary_code"] == "rir"

    listing = client.get("/ontologies/candidates/dictionaries")
    assert listing.status_code == 200
    assert listing.json()["count"] == 1

    candidate = client.get("/ontologies/candidates/dictionaries/cand_123")
    assert candidate.status_code == 200
    assert candidate.json()["candidate"]["id"] == "cand_123"

    download = client.get("/ontologies/candidates/dictionaries/cand_123/json")
    assert download.status_code == 200
    assert download.json()["DictionaryCode"] == "rir"

    validate = client.post("/ontologies/candidates/dictionaries/cand_123/validate")
    assert validate.status_code == 200
    validate_body = validate.json()
    assert validate_body["class_count"] == 1
    assert validate_body["property_count"] == 1
    assert validate_body["ttl_has_ontology"] is True

    convert = client.post("/ontologies/candidates/dictionaries/cand_123/convert")
    assert convert.status_code == 200
    convert_body = convert.json()
    assert convert_body["class_count"] == 1
    assert convert_body["validation_report"]["class_count"] == 1
    assert "owl:Ontology" in convert_body["ttl"]

    publish = client.post(
        "/ontologies/candidates/dictionaries/cand_123/publish",
        json={
            "status": "candidate",
            "issued_at": "2026-03-25",
            "default_version": True,
            "output_resource_filename": "rir-global.ttl",
        },
    )
    assert publish.status_code == 200
    publish_body = publish.json()
    assert publish_body["published"] is True
    assert publish_body["publish_result"]["resource_filename"] == "rir-global.ttl"

    delete = client.delete("/ontologies/candidates/dictionaries/cand_123")
    assert delete.status_code == 200
    assert delete.json() == {"deleted": True, "candidate_id": "cand_123"}
