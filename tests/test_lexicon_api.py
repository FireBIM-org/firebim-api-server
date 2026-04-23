import os
from types import SimpleNamespace

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

from app.api import lexicon as lexicon_api
from app.lexicon import service as lexicon_service
from app.lexicon.service import LexiconService

ONTOLOGY_TTL = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix fbo-dk: <https://firebim.org/ontologies/fbo/dk#> .
<https://firebim.org/ontologies/fbo/dk#> a owl:Ontology .
fbo-dk:Wall a owl:Class .
fbo-dk:Combustible a owl:DatatypeProperty .
fbo-dk:hasPart a owl:ObjectProperty .
"""


class DummyOntologyRepo:
    def health(self) -> bool:
        return True

    def fetch_version(self, ontology_family: str, country: str, version: str):
        resolved = "2026.04.23" if version == "latest" else version
        return SimpleNamespace(
            canonical_family_uri=f"https://firebim.org/ontologies/{ontology_family}#",
            canonical_country_uri=f"https://firebim.org/ontologies/{ontology_family}/{country}#",
            ttl=ONTOLOGY_TTL,
            version=SimpleNamespace(
                version=resolved,
                ontology_uri=f"https://firebim.org/ontologies/{ontology_family}/{country}#",
                version_iri=f"https://firebim.org/ontologies/{ontology_family}/{country}/{resolved}#",
            ),
        )


def _build_client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(lexicon_api.router)
    monkeypatch.setattr(lexicon_api, "service", LexiconService(DummyOntologyRepo()))
    return TestClient(app)


def test_lexicon_health(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.get("/lexicon/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ontologies_connected": True,
    }


def test_derive_execution_profile_from_inline_rule(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/profiles/derive",
        json={
            "ontology_family": "fbo",
            "country": "dk",
            "version": "latest",
            "rule": {
                "id": "wall_rule",
                "entry": "start",
                "elements": [
                    {
                        "id": "start",
                        "element_type": "decision",
                        "target_class": "Wall",
                        "property_path": "Combustible",
                        "operator": "=",
                        "value": True,
                        "true_path": "ok",
                        "false_path": "fail",
                        "unknown_path": "unknown",
                        "label": "Check combustibility",
                        "config": {},
                    },
                    {"id": "ok", "element_type": "end", "label": "Ok", "config": {}},
                    {"id": "fail", "element_type": "end", "label": "Fail", "config": {}},
                    {"id": "unknown", "element_type": "end", "label": "Unknown", "config": {}},
                ],
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "2026.04.23"
    assert body["profile"]["rule_id"] == "wall_rule"
    assert body["profile"]["target_classes"] == ["Wall"]
    assert body["profile"]["property_paths"] == ["Combustible"]
    assert body["profile"]["targets"][0]["property_requirements"][0]["property_path"] == "Combustible"


def test_derive_execution_profile_from_published_rule(monkeypatch):
    client = _build_client(monkeypatch)
    monkeypatch.setattr(
        lexicon_service,
        "fetch_rule_ttl",
        lambda rule_id: (
            f"https://firebim.org/graphs/rulebook/{rule_id}",
            "@prefix ex: <http://example.org/> .",
        ),
    )
    monkeypatch.setattr(
        lexicon_service,
        "decompile_ruleset_ttl",
        lambda ttl: {
            "id": "published_rule",
            "entry": "decide",
            "nodes": [
                {
                    "id": "decide",
                    "node_type": "decision",
                    "target_class": "Wall",
                    "property_path": "Combustible",
                    "operator": "=",
                    "value": True,
                    "true_path": "ok",
                },
                {"id": "ok", "node_type": "end"},
            ],
        },
    )
    response = client.post(
        "/lexicon/profiles/derive",
        json={
            "ontology_family": "fbo",
            "country": "dk",
            "version": "latest",
            "rule_id": "published_rule",
        },
    )
    assert response.status_code == 200
    assert response.json()["profile"]["rule_id"] == "published_rule"


def test_derive_execution_profile_warns_on_property_as_target_class(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/profiles/derive",
        json={
            "ontology_family": "fbo",
            "country": "dk",
            "version": "latest",
            "rule": {
                "id": "bad_rule",
                "entry": "start",
                "elements": [
                    {
                        "id": "start",
                        "element_type": "decision",
                        "target_class": "Combustible",
                        "property_path": "Wall",
                        "operator": "=",
                        "value": True,
                        "true_path": "ok",
                        "false_path": "fail",
                        "unknown_path": "unknown",
                        "config": {},
                    },
                    {"id": "ok", "element_type": "end", "config": {}},
                    {"id": "fail", "element_type": "end", "config": {}},
                    {"id": "unknown", "element_type": "end", "config": {}},
                ],
            },
        },
    )
    assert response.status_code == 200
    warnings = response.json()["profile"]["warnings"]
    assert any("uses property URI as target_class" in item for item in warnings)
    assert any("uses class URI as property_path" in item for item in warnings)


def test_reconstruct_canonical_graph(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/reconstruction",
        json={
            "ontology_family": "fbo",
            "country": "dk",
            "version": "latest",
            "graph_id": "demo-graph",
            "entities": [
                {
                    "entity_id": "wall-1",
                    "class_uri": "Wall",
                    "label": "Wall 1",
                    "properties": [
                        {
                            "property_uri": "Combustible",
                            "value": True,
                        }
                    ],
                    "relations": [],
                }
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["graph_uri"] == "https://firebim.org/graphs/lexicon/demo-graph"
    assert body["triple_count"] == 3
    assert "@prefix ns1: <https://firebim.org/ontologies/fbo/dk#>" in body["ttl"]
    assert "ns1:Wall" in body["ttl"]
    assert "ns1:Combustible true" in body["ttl"]


def test_check_conformity_flags_unknown_terms(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/conformity/check",
        json={
            "ontology_family": "fbo",
            "country": "dk",
            "version": "latest",
            "ttl": """
@prefix ex: <https://firebim.org/instances/> .
@prefix fbo-dk: <https://firebim.org/ontologies/fbo/dk#> .
ex:wall-1 a fbo-dk:Wall ;
    fbo-dk:UnknownProperty true .
""",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["conforms"] is False
    assert body["semantic_ok"] is False
    assert body["unknown_properties"] == ["https://firebim.org/ontologies/fbo/dk#UnknownProperty"]
