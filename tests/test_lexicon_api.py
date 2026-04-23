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

FBO_ONTOLOGY_TTL = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix meta: <https://firebim.org/ontology-meta#> .
@prefix fbo-dk: <https://firebim.org/ontologies/fbo/dk#> .
<https://firebim.org/ontologies/fbo/dk#> a owl:Ontology .
fbo-dk:BuildingElement a owl:Class .
fbo-dk:Wall a owl:Class ;
    rdfs:subClassOf fbo-dk:BuildingElement ;
    meta:hasProperty fbo-dk:Combustible .
fbo-dk:Combustible a owl:DatatypeProperty .
fbo-dk:hasPart a owl:ObjectProperty .
"""

IFC_ONTOLOGY_TTL = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix meta: <https://firebim.org/ontology-meta#> .
@prefix ifc: <https://firebim.org/ontologies/ifc/global#> .
<https://firebim.org/ontologies/ifc/global#> a owl:Ontology .
ifc:IfcBuiltElement a owl:Class .
ifc:IfcWall a owl:Class ;
    rdfs:subClassOf ifc:IfcBuiltElement .
ifc:FireRating a owl:DatatypeProperty .
ifc:MaterialReference a owl:ObjectProperty .
ifc:IfcWallUSERDEFINED a owl:Class ;
    rdfs:subClassOf ifc:IfcWall ;
    rdfs:comment "Technical note: This IFC class is a predefined type represented by its parent class." .
"""


class DummyOntologyRepo:
    def __init__(self) -> None:
        self.fetch_calls = 0

    def health(self) -> bool:
        return True

    def fetch_version(self, ontology_family: str, country: str, version: str):
        self.fetch_calls += 1
        resolved = "2026.04.23" if version == "latest" else version
        ttl = FBO_ONTOLOGY_TTL
        if ontology_family == "ifc":
            ttl = IFC_ONTOLOGY_TTL
        return SimpleNamespace(
            canonical_family_uri=f"https://firebim.org/ontologies/{ontology_family}#",
            canonical_country_uri=f"https://firebim.org/ontologies/{ontology_family}/{country}#",
            ttl=ttl,
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


def _build_client_with_repo(monkeypatch) -> tuple[TestClient, DummyOntologyRepo]:
    app = FastAPI()
    app.include_router(lexicon_api.router)
    repo = DummyOntologyRepo()
    monkeypatch.setattr(lexicon_api, "service", LexiconService(repo))
    return TestClient(app), repo


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


def test_list_catalog_terms(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.get("/lexicon/catalog/fbo/dk/latest/terms?kind=class&search=wall&limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["total_count"] == 1
    assert body["next_cursor"] is None
    assert body["terms"][0]["term_id"] == "Wall"
    assert body["terms"][0]["kind"] == "class"
    assert body["terms"][0]["parent_term_ids"] == ["BuildingElement"]


def test_list_catalog_terms_supports_cursor_and_filters(monkeypatch):
    client = _build_client(monkeypatch)

    first_page = client.get("/lexicon/catalog/fbo/dk/latest/terms?limit=1")
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert first_body["count"] == 1
    assert first_body["total_count"] == 4
    assert first_body["next_cursor"] == first_body["terms"][0]["term_id"]

    second_page = client.get(f"/lexicon/catalog/fbo/dk/latest/terms?limit=1&cursor={first_body['next_cursor']}")
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert second_body["count"] == 1
    assert second_body["terms"][0]["term_id"] != first_body["terms"][0]["term_id"]

    property_filter = client.get("/lexicon/catalog/fbo/dk/latest/terms?kind=property&property_of=Wall")
    assert property_filter.status_code == 200
    property_body = property_filter.json()
    assert property_body["count"] == 1
    assert property_body["terms"][0]["term_id"] == "Combustible"

    predefined_filter = client.get("/lexicon/catalog/ifc/global/latest/terms?is_predefined_type=true")
    assert predefined_filter.status_code == 200
    predefined_body = predefined_filter.json()
    assert predefined_body["count"] == 1
    assert predefined_body["terms"][0]["term_id"] == "IfcWallUSERDEFINED"


def test_catalog_uses_cached_ontology_bundle(monkeypatch):
    client, repo = _build_client_with_repo(monkeypatch)

    first_response = client.get("/lexicon/catalog/fbo/dk/latest/terms?kind=class")
    assert first_response.status_code == 200
    second_response = client.get("/lexicon/catalog/fbo/dk/latest/terms/Wall")
    assert second_response.status_code == 200

    assert repo.fetch_calls == 1


def test_get_catalog_term(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.get("/lexicon/catalog/fbo/dk/latest/terms/Wall")
    assert response.status_code == 200
    body = response.json()
    assert body["term"]["term_id"] == "Wall"
    assert body["term"]["parent_term_ids"] == ["BuildingElement"]
    assert body["term"]["property_term_ids"] == ["Combustible"]


def test_validate_mapping_allows_class_to_subclass(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/mappings/validate",
        json={
            "mapping_kind": "class_to_class",
            "source_term": {
                "ontology_family": "fbo",
                "country": "dk",
                "version": "latest",
                "term_id": "Wall",
            },
            "target_term": {
                "ontology_family": "ifc",
                "country": "global",
                "version": "latest",
                "term_id": "IfcWallUSERDEFINED",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["source_kind"] == "class"
    assert body["target_kind"] == "class"
    assert body["compatible"] is True
    assert any("subclass or predefined type" in item for item in body["warnings"])


def test_validate_mapping_rejects_property_to_class(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/mappings/validate",
        json={
            "mapping_kind": "property_to_property",
            "source_term": {
                "ontology_family": "fbo",
                "country": "dk",
                "version": "latest",
                "term_id": "Combustible",
            },
            "target_term": {
                "ontology_family": "ifc",
                "country": "global",
                "version": "latest",
                "term_id": "IfcWall",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["source_kind"] == "property"
    assert body["target_kind"] == "class"
    assert any("Target term must be a property" in item for item in body["errors"])


def test_validate_semantic_assertion_all_of(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/mappings/validate",
        json={
            "mapping_kind": "semantic_assertion",
            "source_pattern": {
                "all_of": [
                    {
                        "term": {
                            "term_kind": "class",
                            "ontology_family": "ifc",
                            "country": "global",
                            "version": "latest",
                            "term_id": "IfcWall",
                        }
                    },
                    {
                        "term": {
                            "term_kind": "property",
                            "ontology_family": "ifc",
                            "country": "global",
                            "version": "latest",
                            "term_id": "FireRating",
                        }
                    },
                ]
            },
            "target_assertions": [
                {
                    "assertion_kind": "class",
                    "ontology_family": "fbo",
                    "country": "dk",
                    "version": "latest",
                    "term_id": "Wall",
                },
                {
                    "assertion_kind": "property_value",
                    "ontology_family": "fbo",
                    "country": "dk",
                    "version": "latest",
                    "term_id": "Combustible",
                    "value": True,
                },
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["compatible"] is True
    assert any("many-are-one" in item or "many-can-be-one" in item for item in body["warnings"])


def test_validate_semantic_assertion_any_of(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/mappings/validate",
        json={
            "mapping_kind": "semantic_assertion",
            "source_pattern": {
                "any_of": [
                    {
                        "term": {
                            "term_kind": "property",
                            "ontology_family": "ifc",
                            "country": "global",
                            "version": "latest",
                            "term_id": "FireRating",
                        }
                    },
                    {
                        "term": {
                            "term_kind": "property",
                            "ontology_family": "ifc",
                            "country": "global",
                            "version": "latest",
                            "term_id": "MaterialReference",
                        }
                    },
                ]
            },
            "target_assertions": [
                {
                    "assertion_kind": "property_value",
                    "ontology_family": "fbo",
                    "country": "dk",
                    "version": "latest",
                    "term_id": "Combustible",
                    "value": True,
                }
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["compatible"] is True


def test_validate_semantic_assertion_rejects_wrong_source_kind(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/mappings/validate",
        json={
            "mapping_kind": "semantic_assertion",
            "source_pattern": {
                "all_of": [
                    {
                        "term": {
                            "term_kind": "property",
                            "ontology_family": "ifc",
                            "country": "global",
                            "version": "latest",
                            "term_id": "IfcWall",
                        }
                    }
                ]
            },
            "target_assertions": [
                {
                    "assertion_kind": "class",
                    "ontology_family": "fbo",
                    "country": "dk",
                    "version": "latest",
                    "term_id": "Wall",
                }
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any("was declared as property but resolves to class" in item for item in body["errors"])
