import os
from types import SimpleNamespace
from uuid import uuid4

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
from app.schemas.lexicon import (
    LexiconExecutionProfile,
    LexiconExecutionProfileNode,
    LexiconExecutionTarget,
    LexiconHandshake,
    LexiconHandshakeAcceptedPayload,
    LexiconMapping,
    LexiconMappingSummary,
    LexiconProfileSummaryItem,
    LexiconPropertyRequirement,
    LexiconStoredProfile,
)

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
fbo-dk:WallProperties a owl:Class ;
    meta:groupOfProperties true ;
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
ifc:ReferenceRating a owl:DatatypeProperty .
ifc:MaterialReference a owl:ObjectProperty .
ifc:FireRating meta:unit "min" ;
    meta:belongsToClass ifc:IfcWall ;
    meta:relatedProperty ifc:ReferenceRating .
ifc:WallCommonProperties a owl:Class ;
    meta:groupOfProperties true ;
    meta:hasProperty ifc:FireRating .
ifc:IfcWallUSERDEFINED a owl:Class ;
    rdfs:subClassOf ifc:IfcWall ;
    rdfs:comment "Technical note: This IFC class is a predefined type represented by its parent class." .
"""

BUILDING_GRAPH_TTL = """
@prefix ifc: <https://firebim.org/ontologies/ifc/global#> .
@prefix fbo: <https://firebim.org/ontologies/fbo/dk#> .
@prefix ex: <https://example.org/buildings/> .
ex:wall-1 a ifc:IfcWall ;
    ifc:FireRating "EI60" ;
    fbo:Combustible true .
ex:wall-2 a ifc:IfcWallUSERDEFINED ;
    ifc:MaterialReference ex:material-1 .
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


class DummyBuildingRepo:
    def fetch_graph(self, graph_id: str):
        return f"https://firebim.org/graphs/buildings/{graph_id}", BUILDING_GRAPH_TTL


def _build_client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(lexicon_api.router)
    monkeypatch.setattr(lexicon_api, "service", LexiconService(DummyOntologyRepo(), DummyBuildingRepo()))
    app.dependency_overrides[lexicon_api.get_current_active_user] = lambda: SimpleNamespace(
        id="user-1",
        is_active=True,
    )
    return TestClient(app)


def _build_client_with_repo(monkeypatch) -> tuple[TestClient, DummyOntologyRepo]:
    app = FastAPI()
    app.include_router(lexicon_api.router)
    repo = DummyOntologyRepo()
    monkeypatch.setattr(lexicon_api, "service", LexiconService(repo, DummyBuildingRepo()))
    app.dependency_overrides[lexicon_api.get_current_active_user] = lambda: SimpleNamespace(
        id="user-1",
        is_active=True,
    )
    return TestClient(app), repo


class DummyMappingRepo:
    def __init__(self) -> None:
        self.created_payload = None
        self.deleted_id = None
        self.mapping = LexiconMapping(
            id="map_123",
            name="IfcWall to Wall",
            mapping_kind="class_to_class",
            source_term={
                "ontology_family": "ifc",
                "country": "global",
                "version": "latest",
                "term_id": "IfcWall",
                "uri": None,
            },
            target_term={
                "ontology_family": "fbo",
                "country": "dk",
                "version": "latest",
                "term_id": "Wall",
                "uri": None,
            },
            source_pattern=None,
            target_assertions=[],
            relationship="equivalent",
            notes="Chosen by frontend",
            created_at="2026-04-23T10:00:00+00:00",
            updated_at="2026-04-23T10:00:00+00:00",
        )
        self.semantic_mapping = LexiconMapping(
            id="map_semantic",
            name="Wall combustible assertion",
            mapping_kind="semantic_assertion",
            source_term=None,
            target_term=None,
            source_pattern={
                "all_of": [
                    {
                        "term": {
                            "term_kind": "class",
                            "ontology_family": "ifc",
                            "country": "global",
                            "version": "latest",
                            "term_id": "IfcWall",
                        }
                    }
                ]
            },
            target_assertions=[
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
            relationship="supports",
            notes="Chosen by frontend",
            created_at="2026-04-23T10:00:00+00:00",
            updated_at="2026-04-23T10:00:00+00:00",
        )

    def create_mapping(self, payload, user):
        _ = user
        self.created_payload = payload
        return self.mapping

    def list_mappings(self, user):
        _ = user
        return [
            LexiconMappingSummary(
                id="map_123",
                name="IfcWall to Wall",
                mapping_kind="class_to_class",
                relationship="equivalent",
                created_at="2026-04-23T10:00:00+00:00",
                updated_at="2026-04-23T10:00:00+00:00",
                source_term_count=1,
                target_assertion_count=1,
            )
        ]

    def get_mapping(self, mapping_id, user):
        _ = user
        if mapping_id == "map_semantic":
            return self.semantic_mapping
        return self.mapping.model_copy(update={"id": mapping_id})

    def delete_mapping(self, mapping_id, user):
        _ = user
        self.deleted_id = mapping_id


class DummyHandshakeRepo:
    def __init__(self) -> None:
        self.handshake = LexiconHandshake(
            id="hs_123",
            ontology_family="fbo",
            country="dk",
            version="2026.04.23",
            canonical_family_uri="https://firebim.org/ontologies/fbo#",
            canonical_country_uri="https://firebim.org/ontologies/fbo/dk#",
            selected_ontologies=[
                {
                    "ontology_family": "fbo",
                    "country": "dk",
                    "version": "2026.04.23",
                    "canonical_family_uri": "https://firebim.org/ontologies/fbo#",
                    "canonical_country_uri": "https://firebim.org/ontologies/fbo/dk#",
                }
            ],
            canonical_target_ontology={
                "ontology_family": "fbo",
                "country": "dk",
                "version": "2026.04.23",
                "canonical_family_uri": "https://firebim.org/ontologies/fbo#",
                "canonical_country_uri": "https://firebim.org/ontologies/fbo/dk#",
            },
            profile=LexiconExecutionProfile(
                rule_id="wall_rule",
                entry_node_id="start",
                node_count=4,
                complete=True,
                open=False,
                broken=False,
                target_classes=["Wall"],
                property_paths=["Combustible"],
                relation_paths=[],
                targets=[
                    LexiconExecutionTarget(
                        target_class="Wall",
                        node_ids=["start"],
                        property_requirements=[
                            LexiconPropertyRequirement(
                                property_path="Combustible",
                                node_ids=["start"],
                                operators=["="],
                                sample_values=["True"],
                            )
                        ],
                        relation_requirements=[],
                    )
                ],
                nodes=[
                    LexiconExecutionProfileNode(
                        node_id="start",
                        element_type="decision",
                        label="Check combustibility",
                        target_class="Wall",
                        property_path="Combustible",
                        operator="=",
                        value=True,
                        branches=[],
                    )
                ],
                warnings=[],
            ),
            accepted_payload=LexiconHandshakeAcceptedPayload(),
            created_at="2026-04-23T10:00:00+00:00",
            updated_at="2026-04-23T10:00:00+00:00",
        )
        self.created = None
        self.updated_payload = None

    def create_handshake(
        self,
        *,
        user,
        ontology_family,
        country,
        version,
        canonical_family_uri,
        canonical_country_uri,
        selected_ontologies,
        canonical_target_ontology,
        profile,
        accepted_payload,
    ):
        _ = user
        self.created = {
            "ontology_family": ontology_family,
            "country": country,
            "version": version,
            "accepted_payload": accepted_payload,
        }
        self.handshake = self.handshake.model_copy(
            update={
                "ontology_family": ontology_family,
                "country": country,
                "version": version,
                "canonical_family_uri": canonical_family_uri,
                "canonical_country_uri": canonical_country_uri,
                "selected_ontologies": selected_ontologies,
                "canonical_target_ontology": canonical_target_ontology,
                "profile": profile,
                "accepted_payload": accepted_payload,
            }
        )
        return self.handshake

    def get_handshake(self, handshake_id, user):
        _ = user
        return self.handshake.model_copy(update={"id": handshake_id})

    def update_accepted_payload(self, handshake_id, accepted_payload, user):
        _ = user
        self.updated_payload = accepted_payload
        self.handshake = self.handshake.model_copy(
            update={
                "id": handshake_id,
                "accepted_payload": accepted_payload,
                "updated_at": "2026-04-23T11:00:00+00:00",
            }
        )
        return self.handshake


def _build_client_with_mapping_repo(monkeypatch) -> tuple[TestClient, DummyMappingRepo]:
    app = FastAPI()
    app.include_router(lexicon_api.router)
    mapping_repo = DummyMappingRepo()
    monkeypatch.setattr(lexicon_api, "service", LexiconService(DummyOntologyRepo(), DummyBuildingRepo()))
    monkeypatch.setattr(lexicon_api, "mapping_repo", mapping_repo)
    app.dependency_overrides[lexicon_api.get_current_active_user] = lambda: SimpleNamespace(
        id=f"user-{uuid4()}",
        is_active=True,
    )
    return TestClient(app), mapping_repo


def _build_client_with_handshake_repo(monkeypatch) -> tuple[TestClient, DummyHandshakeRepo]:
    app = FastAPI()
    app.include_router(lexicon_api.router)
    handshake_repo = DummyHandshakeRepo()
    monkeypatch.setattr(lexicon_api, "service", LexiconService(DummyOntologyRepo(), DummyBuildingRepo()))
    monkeypatch.setattr(lexicon_api, "handshake_repo", handshake_repo)
    monkeypatch.setattr(lexicon_api, "mapping_repo", DummyMappingRepo())
    app.dependency_overrides[lexicon_api.get_current_active_user] = lambda: SimpleNamespace(
        id=f"user-{uuid4()}",
        is_active=True,
    )
    return TestClient(app), handshake_repo


class DummyProfileRepo:
    def __init__(self) -> None:
        self.profile = LexiconStoredProfile(
            id="prof_123",
            name="Wall profile",
            description="Profile for walls",
            ontology_family="fbo",
            country="dk",
            version="2026.04.23",
            canonical_family_uri="https://firebim.org/ontologies/fbo#",
            canonical_country_uri="https://firebim.org/ontologies/fbo/dk#",
            selected_ontologies=[
                {
                    "ontology_family": "fbo",
                    "country": "dk",
                    "version": "2026.04.23",
                    "canonical_family_uri": "https://firebim.org/ontologies/fbo#",
                    "canonical_country_uri": "https://firebim.org/ontologies/fbo/dk#",
                },
                {
                    "ontology_family": "ifc",
                    "country": "global",
                    "version": "2026.04.23",
                    "canonical_family_uri": "https://firebim.org/ontologies/ifc#",
                    "canonical_country_uri": "https://firebim.org/ontologies/ifc/global#",
                },
            ],
            canonical_target_ontology={
                "ontology_family": "fbo",
                "country": "dk",
                "version": "2026.04.23",
                "canonical_family_uri": "https://firebim.org/ontologies/fbo#",
                "canonical_country_uri": "https://firebim.org/ontologies/fbo/dk#",
            },
            rule_ids=["wall_rule"],
            keywords=["wall", "fire"],
            profile=LexiconExecutionProfile(
                rule_id="wall_rule",
                rule_ids=["wall_rule"],
                entry_node_id="start",
                node_count=4,
                complete=True,
                open=False,
                broken=False,
                target_classes=["Wall"],
                property_paths=["Combustible"],
                relation_paths=[],
                targets=[
                    LexiconExecutionTarget(
                        target_class="Wall",
                        node_ids=["start"],
                        property_requirements=[
                            LexiconPropertyRequirement(
                                property_path="Combustible",
                                node_ids=["start"],
                                operators=["="],
                                sample_values=["True"],
                            )
                        ],
                        relation_requirements=[],
                    )
                ],
                nodes=[
                    LexiconExecutionProfileNode(
                        node_id="start",
                        element_type="decision",
                        label="Check combustibility",
                        target_class="Wall",
                        property_path="Combustible",
                        operator="=",
                        value=True,
                        branches=[],
                    )
                ],
                warnings=[],
            ),
            rule_profiles=[
                LexiconExecutionProfile(
                    rule_id="wall_rule",
                    rule_ids=["wall_rule"],
                    entry_node_id="start",
                    node_count=4,
                    complete=True,
                    open=False,
                    broken=False,
                    target_classes=["Wall"],
                    property_paths=["Combustible"],
                    relation_paths=[],
                    targets=[
                        LexiconExecutionTarget(
                            target_class="Wall",
                            node_ids=["start"],
                            property_requirements=[
                                LexiconPropertyRequirement(
                                    property_path="Combustible",
                                    node_ids=["start"],
                                    operators=["="],
                                    sample_values=["True"],
                                )
                            ],
                            relation_requirements=[],
                        )
                    ],
                    nodes=[],
                    warnings=[],
                )
            ],
            created_at="2026-04-23T10:00:00+00:00",
            updated_at="2026-04-23T10:00:00+00:00",
        )
        self.created_payload = None
        self.updated_profile = None
        self.deleted_id = None

    def create_profile(
        self,
        *,
        payload,
        user,
        canonical_family_uri,
        canonical_country_uri,
        rule_ids,
        profile,
        rule_profiles,
        selected_ontologies,
        canonical_target_ontology,
    ):
        _ = user
        self.created_payload = payload
        self.profile = self.profile.model_copy(
            update={
                "name": payload.name or self.profile.name,
                "description": payload.description,
                "canonical_family_uri": canonical_family_uri,
                "canonical_country_uri": canonical_country_uri,
                "rule_ids": rule_ids,
                "profile": profile,
                "rule_profiles": rule_profiles,
                "selected_ontologies": selected_ontologies,
                "canonical_target_ontology": canonical_target_ontology,
            }
        )
        return self.profile

    def update_profile(self, *, profile_id, user, updated):
        _ = user
        self.updated_profile = updated
        self.profile = updated.model_copy(update={"id": profile_id})
        return self.profile

    def get_profile(self, profile_id, user):
        _ = user
        return self.profile.model_copy(update={"id": profile_id})

    def list_profiles(self, user):
        _ = user
        return [
            LexiconProfileSummaryItem(
                id=self.profile.id,
                name=self.profile.name,
                description=self.profile.description,
                ontology_family=self.profile.ontology_family,
                country=self.profile.country,
                version=self.profile.version,
                rule_ids=self.profile.rule_ids,
                selected_ontologies=self.profile.selected_ontologies,
                keywords=self.profile.keywords,
                target_count=len(self.profile.profile.targets),
                property_requirement_count=sum(
                    len(target.property_requirements) for target in self.profile.profile.targets
                ),
                updated_at=self.profile.updated_at,
            )
        ]

    def delete_profile(self, profile_id, user):
        _ = user
        self.deleted_id = profile_id


def _build_client_with_profile_repo(monkeypatch) -> tuple[TestClient, DummyProfileRepo]:
    app = FastAPI()
    app.include_router(lexicon_api.router)
    profile_repo = DummyProfileRepo()
    monkeypatch.setattr(lexicon_api, "service", LexiconService(DummyOntologyRepo(), DummyBuildingRepo()))
    monkeypatch.setattr(lexicon_api, "profile_repo", profile_repo)
    monkeypatch.setattr(lexicon_api, "mapping_repo", DummyMappingRepo())
    app.dependency_overrides[lexicon_api.get_current_active_user] = lambda: SimpleNamespace(
        id=f"user-{uuid4()}",
        is_active=True,
    )
    return TestClient(app), profile_repo


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
    assert body["selected_ontologies"][0]["ontology_family"] == "fbo"
    assert body["canonical_target_ontology"]["ontology_family"] == "fbo"


def test_derive_execution_profile_with_multiple_selected_ontologies(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/profiles/derive",
        json={
            "ontology_family": "fbo",
            "country": "dk",
            "version": "latest",
            "selected_ontologies": [
                {
                    "ontology_family": "ifc",
                    "country": "global",
                    "version": "latest",
                },
                {
                    "ontology_family": "fbo",
                    "country": "dk",
                    "version": "latest",
                },
            ],
            "canonical_target_ontology": {
                "ontology_family": "fbo",
                "country": "dk",
                "version": "latest",
            },
            "rule": {
                "id": "wall_rule",
                "entry": "start",
                "elements": [
                    {
                        "id": "start",
                        "element_type": "decision",
                        "target_class": "IfcWall",
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
    assert body["selected_ontologies"][0]["ontology_family"] == "ifc"
    assert body["selected_ontologies"][1]["ontology_family"] == "fbo"
    assert body["canonical_target_ontology"]["ontology_family"] == "fbo"
    assert body["profile"]["warnings"] == []


def test_profile_summary(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/profiles/summary",
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
    assert body["rule_id"] == "wall_rule"
    assert body["target_count"] == 1
    assert body["property_requirement_count"] == 1
    assert body["publish_ready"] is True


def test_derive_execution_profile_from_rule_set(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/profiles/derive",
        json={
            "ontology_family": "fbo",
            "country": "dk",
            "version": "latest",
            "rules": [
                {
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
                {
                    "id": "wall_rule_2",
                    "entry": "start_2",
                    "elements": [
                        {
                            "id": "start_2",
                            "element_type": "decision",
                            "target_class": "Wall",
                            "property_path": "Combustible",
                            "operator": "=",
                            "value": True,
                            "true_path": "ok_2",
                            "false_path": "fail_2",
                            "unknown_path": "unknown_2",
                            "label": "Check combustibility again",
                            "config": {},
                        },
                        {"id": "ok_2", "element_type": "end", "label": "Ok", "config": {}},
                        {"id": "fail_2", "element_type": "end", "label": "Fail", "config": {}},
                        {"id": "unknown_2", "element_type": "end", "label": "Unknown", "config": {}},
                    ],
                },
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rule_ids"] == ["wall_rule", "wall_rule_2"]
    assert len(body["rule_profiles"]) == 2
    assert body["profile"]["rule_id"] == "multi-rule-profile"


def test_create_list_get_update_and_delete_profile(monkeypatch):
    client, profile_repo = _build_client_with_profile_repo(monkeypatch)

    create = client.post(
        "/lexicon/profiles",
        json={
            "name": "Wall profile",
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
            "keywords": ["wall"],
        },
    )
    assert create.status_code == 201
    assert create.json()["profile_record"]["name"] == "Wall profile"
    assert profile_repo.created_payload.name == "Wall profile"

    listing = client.get("/lexicon/profiles?search=wall")
    assert listing.status_code == 200
    assert listing.json()["count"] == 1

    fetched = client.get("/lexicon/profiles/prof_123")
    assert fetched.status_code == 200
    assert fetched.json()["profile_record"]["id"] == "prof_123"

    update = client.put(
        "/lexicon/profiles/prof_123",
        json={"description": "Updated profile"},
    )
    assert update.status_code == 200
    assert update.json()["profile_record"]["description"] == "Updated profile"

    delete = client.delete("/lexicon/profiles/prof_123")
    assert delete.status_code == 200
    assert delete.json() == {"deleted": True, "profile_id": "prof_123"}
    assert profile_repo.deleted_id == "prof_123"


def test_profile_rules_coverage(monkeypatch):
    client, _profile_repo = _build_client_with_profile_repo(monkeypatch)
    response = client.post("/lexicon/profiles/prof_123/rules/coverage", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["profile_id"] == "prof_123"
    assert body["fully_supported_rule_ids"] == ["wall_rule"]


def test_profile_graph_coverage(monkeypatch):
    client, _profile_repo = _build_client_with_profile_repo(monkeypatch)
    response = client.post("/lexicon/profiles/prof_123/graph-coverage", json={"graph_id": "demo-graph"})
    assert response.status_code == 200
    body = response.json()
    assert body["profile_id"] == "prof_123"
    assert body["complete"] is False
    assert body["covered_target_classes"] == []
    assert body["missing_target_classes"] == ["Wall"]


def test_profile_mapping_candidates(monkeypatch):
    client, _profile_repo = _build_client_with_profile_repo(monkeypatch)
    response = client.get("/lexicon/profiles/prof_123/mappings/candidates")
    assert response.status_code == 200
    body = response.json()
    assert body["profile_id"] == "prof_123"
    assert body["count"] >= 1


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
    assert body["profile_alignment_ok"] is True


def test_check_conformity_reports_profile_gaps(monkeypatch):
    service = LexiconService(DummyOntologyRepo())
    _ontology, profile, _rule_profiles, _rule_ids = service.derive_profile(
        ontology_family="fbo",
        country="dk",
        version="latest",
        rule=SimpleNamespace(
            as_internal_dict=lambda: {
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
                    }
                ],
            }
        ),
    )
    response = service.check_conformity(
        ontology_family="fbo",
        country="dk",
        version="latest",
        ttl="""
@prefix ex: <https://firebim.org/instances/> .
@prefix fbo-dk: <https://firebim.org/ontologies/fbo/dk#> .
ex:wall-1 a fbo-dk:Wall .
""",
        expected_profile=profile,
    )
    assert response.profile_alignment_ok is False
    assert response.missing_profile_target_classes == []
    assert response.missing_profile_properties[0].property_path == "Combustible"


def test_list_catalog_terms(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.get("/lexicon/catalog/fbo/dk/latest/terms?kind=class&search=wall&limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert body["total_count"] == 2
    assert body["next_cursor"] is None
    assert [item["term_id"] for item in body["terms"]] == ["Wall", "WallProperties"]
    assert body["terms"][0]["kind"] == "class"
    assert body["terms"][0]["parent_term_ids"] == ["BuildingElement"]


def test_list_catalog_terms_supports_cursor_and_filters(monkeypatch):
    client = _build_client(monkeypatch)

    first_page = client.get("/lexicon/catalog/fbo/dk/latest/terms?limit=1")
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert first_body["count"] == 1
    assert first_body["total_count"] == 5
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
    assert body["term"]["property_set_term_ids"] == ["WallProperties"]


def test_resolve_catalog_terms(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/catalog/resolve-terms",
        json={
            "selected_ontologies": [
                {"ontology_family": "ifc", "country": "global", "version": "latest"},
                {"ontology_family": "fbo", "country": "dk", "version": "latest"},
            ],
            "terms": ["IfcWall", "Combustible"],
            "limit_per_term": 5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert body["results"][0]["matches"][0]["term"]["term_id"] == "IfcWall"
    assert body["results"][0]["matches"][0]["match_type"] == "exact_term_id"
    assert body["results"][1]["matches"][0]["term"]["term_id"] == "Combustible"


def test_compare_catalog_terms(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/catalog/compare",
        json={
            "left": {
                "ontology_family": "fbo",
                "country": "dk",
                "version": "latest",
                "term_id": "Wall",
            },
            "right": {
                "ontology_family": "ifc",
                "country": "global",
                "version": "latest",
                "term_id": "IfcWall",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["same_kind"] is True
    assert body["compatible_for_mapping"] is True
    assert any("Class-to-class mapping" in note for note in body["notes"])


def test_catalog_similarity(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/catalog/similarity",
        json={
            "left": {
                "ontology_family": "fbo",
                "country": "dk",
                "version": "latest",
                "term_id": "Wall",
            },
            "right": {
                "ontology_family": "ifc",
                "country": "global",
                "version": "latest",
                "term_id": "IfcWall",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert 0.0 <= body["score"] <= 1.0
    assert any(component["name"] == "kind_match" for component in body["components"])


def test_catalog_similarity_matrix(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/catalog/similarity-matrix",
        json={
            "left_terms": [
                {
                    "ontology_family": "fbo",
                    "country": "dk",
                    "version": "latest",
                    "term_id": "Wall",
                }
            ],
            "right_terms": [
                {
                    "ontology_family": "ifc",
                    "country": "global",
                    "version": "latest",
                    "term_id": "IfcWall",
                },
                {
                    "ontology_family": "ifc",
                    "country": "global",
                    "version": "latest",
                    "term_id": "IfcWallUSERDEFINED",
                },
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["left_count"] == 1
    assert body["right_count"] == 2
    assert len(body["cells"]) == 2


def test_rule_ontology_similarity_with_multiple_rules_and_ontologies(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/catalog/rule-ontology-similarity",
        json={
            "selected_ontologies": [
                {"ontology_family": "ifc", "country": "global", "version": "latest"},
                {"ontology_family": "fbo", "country": "dk", "version": "latest"},
            ],
            "rules": [
                {
                    "id": "ifc_rule",
                    "entry": "start",
                    "elements": [
                        {
                            "id": "start",
                            "element_type": "decision",
                            "target_class": "https://firebim.org/ontologies/ifc/global#IfcWall",
                            "property_path": "https://firebim.org/ontologies/ifc/global#FireRating",
                            "operator": "=",
                            "value": "EI60",
                            "true_path": "ok",
                            "false_path": "fail",
                            "unknown_path": "unknown",
                            "label": "Check IFC wall",
                            "config": {},
                        },
                        {"id": "ok", "element_type": "end", "label": "Ok", "config": {}},
                        {"id": "fail", "element_type": "end", "label": "Fail", "config": {}},
                        {"id": "unknown", "element_type": "end", "label": "Unknown", "config": {}},
                    ],
                },
                {
                    "id": "fbo_rule",
                    "entry": "start",
                    "elements": [
                        {
                            "id": "start",
                            "element_type": "decision",
                            "target_class": "https://firebim.org/ontologies/fbo/dk#Wall",
                            "property_path": "https://firebim.org/ontologies/fbo/dk#Combustible",
                            "operator": "=",
                            "value": True,
                            "true_path": "ok",
                            "false_path": "fail",
                            "unknown_path": "unknown",
                            "label": "Check FBO wall",
                            "config": {},
                        },
                        {"id": "ok", "element_type": "end", "label": "Ok", "config": {}},
                        {"id": "fail", "element_type": "end", "label": "Fail", "config": {}},
                        {"id": "unknown", "element_type": "end", "label": "Unknown", "config": {}},
                    ],
                },
            ],
            "limit_per_term": 2,
            "compact_terms": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rule_ids"] == ["ifc_rule", "fbo_rule"]
    assert body["limit_per_term"] == 2
    assert body["compact_terms"] is True
    assert body["stats"]["rule_count"] == 2
    assert body["stats"]["ontology_count"] == 2
    assert body["stats"]["rule_ontology_pairs"] == 4
    assert len(body["results"]) == 2
    ifc_rule = next(item for item in body["results"] if item["rule_id"] == "ifc_rule")
    assert ifc_rule["target_classes"][0]["source_term"]["local_name"] == "IfcWall"
    assert ifc_rule["target_classes"][0]["source_term"]["value"] == "IfcWall"
    assert any(match["term_id"] == "IfcWall" for match in ifc_rule["target_classes"][0]["matches"])
    assert any(match["ontology_family"] == "fbo" for match in ifc_rule["target_classes"][0]["matches"])
    assert len(body["rule_ontology_summaries"]) == 4
    assert any(
        item["rule_id"] == "ifc_rule" and item["ontology_family"] == "ifc"
        for item in body["rule_ontology_summaries"]
    )


def test_property_and_property_set_related_catalog_endpoints(monkeypatch):
    client = _build_client(monkeypatch)

    class_properties = client.get("/lexicon/catalog/fbo/dk/latest/classes/Wall/properties")
    assert class_properties.status_code == 200
    assert class_properties.json()["terms"][0]["term_id"] == "Combustible"

    class_property_sets = client.get("/lexicon/catalog/fbo/dk/latest/classes/Wall/property-sets")
    assert class_property_sets.status_code == 200
    assert class_property_sets.json()["terms"][0]["term_id"] == "WallProperties"

    class_children = client.get("/lexicon/catalog/ifc/global/latest/classes/IfcWall/children")
    assert class_children.status_code == 200
    assert class_children.json()["terms"][0]["term_id"] == "IfcWallUSERDEFINED"

    class_parents = client.get("/lexicon/catalog/ifc/global/latest/classes/IfcWall/parents")
    assert class_parents.status_code == 200
    assert class_parents.json()["terms"][0]["term_id"] == "IfcBuiltElement"

    property_sets = client.get("/lexicon/catalog/ifc/global/latest/properties/FireRating/property-sets")
    assert property_sets.status_code == 200
    assert property_sets.json()["terms"][0]["term_id"] == "WallCommonProperties"

    property_classes = client.get("/lexicon/catalog/ifc/global/latest/properties/FireRating/classes")
    assert property_classes.status_code == 200
    assert property_classes.json()["terms"][0]["term_id"] == "IfcWall"

    related_properties = client.get("/lexicon/catalog/ifc/global/latest/properties/FireRating/related-properties")
    assert related_properties.status_code == 200
    assert related_properties.json()["terms"][0]["term_id"] == "ReferenceRating"

    property_set_properties = client.get(
        "/lexicon/catalog/ifc/global/latest/property-sets/WallCommonProperties/properties"
    )
    assert property_set_properties.status_code == 200
    assert property_set_properties.json()["terms"][0]["term_id"] == "FireRating"

    property_detail = client.get("/lexicon/catalog/ifc/global/latest/terms/FireRating")
    assert property_detail.status_code == 200
    term = property_detail.json()["term"]
    assert term["unit"] == "min"
    assert term["property_set_term_ids"] == ["WallCommonProperties"]
    assert term["belongs_to_class_term_ids"] == ["IfcWall"]
    assert term["related_property_term_ids"] == ["ReferenceRating"]


def test_graph_evidence_summary(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/graphs/evidence-summary",
        json={
            "graph_id": "demo-graph",
            "selected_ontologies": [
                {"ontology_family": "ifc", "country": "global", "version": "latest"},
                {"ontology_family": "fbo", "country": "dk", "version": "latest"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["graph_id"] == "demo-graph"
    assert "IfcWall" in body["class_term_ids"]
    assert "FireRating" in body["property_term_ids"]
    assert "WallCommonProperties" in body["property_set_term_ids"]
    assert body["subjects"][0]["source_object_ref"].startswith("https://example.org/buildings/")


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


def test_validate_semantic_assertion_entity_any_of(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/mappings/validate",
        json={
            "mapping_kind": "semantic_assertion",
            "source_pattern": {
                "any_of": [
                    {
                        "entity": {
                            "all_of": [
                                {
                                    "term_kind": "class",
                                    "ontology_family": "ifc",
                                    "country": "global",
                                    "version": "latest",
                                    "term_id": "IfcWall",
                                },
                                {
                                    "term_kind": "property",
                                    "ontology_family": "ifc",
                                    "country": "global",
                                    "version": "latest",
                                    "term_id": "FireRating",
                                },
                            ]
                        }
                    },
                    {
                        "entity": {
                            "all_of": [
                                {
                                    "term_kind": "class",
                                    "ontology_family": "ifc",
                                    "country": "global",
                                    "version": "latest",
                                    "term_id": "IfcWallUSERDEFINED",
                                },
                                {
                                    "term_kind": "property",
                                    "ontology_family": "ifc",
                                    "country": "global",
                                    "version": "latest",
                                    "term_id": "MaterialReference",
                                },
                            ]
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
                }
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert any("same source object" in item for item in body["warnings"])


def test_validate_semantic_assertion_entity_all_of(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/mappings/validate",
        json={
            "mapping_kind": "semantic_assertion",
            "source_pattern": {
                "all_of": [
                    {
                        "entity": {
                            "all_of": [
                                {
                                    "term_kind": "class",
                                    "ontology_family": "ifc",
                                    "country": "global",
                                    "version": "latest",
                                    "term_id": "IfcWall",
                                },
                                {
                                    "term_kind": "property",
                                    "ontology_family": "ifc",
                                    "country": "global",
                                    "version": "latest",
                                    "term_id": "FireRating",
                                },
                            ]
                        }
                    },
                    {
                        "entity": {
                            "all_of": [
                                {
                                    "term_kind": "class",
                                    "ontology_family": "ifc",
                                    "country": "global",
                                    "version": "latest",
                                    "term_id": "IfcWallUSERDEFINED",
                                },
                                {
                                    "term_kind": "property",
                                    "ontology_family": "ifc",
                                    "country": "global",
                                    "version": "latest",
                                    "term_id": "MaterialReference",
                                },
                            ]
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


def test_evaluate_mapping_from_repository(monkeypatch):
    client, _mapping_repo = _build_client_with_mapping_repo(monkeypatch)
    response = client.post(
        "/lexicon/mappings/evaluate",
        json={
            "graph_id": "demo-graph",
            "mapping_id": "map_123",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert "https://example.org/buildings/wall-1" in body["matched_source_objects"]


def test_evaluate_mapping_matches_subclasses_via_custom_eval(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/mappings/evaluate",
        json={
            "graph_id": "demo-graph",
            "mapping": {
                "mapping_kind": "class_to_class",
                "source_term": {
                    "ontology_family": "ifc",
                    "country": "global",
                    "version": "latest",
                    "term_id": "IfcBuiltElement",
                },
                "target_term": {
                    "ontology_family": "fbo",
                    "country": "dk",
                    "version": "latest",
                    "term_id": "Wall",
                },
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert "https://example.org/buildings/wall-1" in body["matched_source_objects"]
    assert "https://example.org/buildings/wall-2" in body["matched_source_objects"]


def test_evaluate_semantic_assertion_entity_pattern(monkeypatch):
    client = _build_client(monkeypatch)
    response = client.post(
        "/lexicon/mappings/evaluate",
        json={
            "graph_id": "demo-graph",
            "mapping": {
                "mapping_kind": "semantic_assertion",
                "source_pattern": {
                    "any_of": [
                        {
                            "entity": {
                                "all_of": [
                                    {
                                        "term_kind": "class",
                                        "ontology_family": "ifc",
                                        "country": "global",
                                        "version": "latest",
                                        "term_id": "IfcWall",
                                    },
                                    {
                                        "term_kind": "property",
                                        "ontology_family": "ifc",
                                        "country": "global",
                                        "version": "latest",
                                        "term_id": "FireRating",
                                    },
                                ]
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
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["clause_evaluation"]["clause_type"] == "any_of"
    assert "https://example.org/buildings/wall-1" in body["matched_source_objects"]


def test_create_mapping(monkeypatch):
    client, mapping_repo = _build_client_with_mapping_repo(monkeypatch)
    response = client.post(
        "/lexicon/mappings",
        json={
            "name": "IfcWall to Wall",
            "mapping_kind": "class_to_class",
            "source_term": {
                "ontology_family": "ifc",
                "country": "global",
                "version": "latest",
                "term_id": "IfcWall",
            },
            "target_term": {
                "ontology_family": "fbo",
                "country": "dk",
                "version": "latest",
                "term_id": "Wall",
            },
            "relationship": "equivalent",
            "notes": "Chosen by frontend",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["mapping"]["id"] == "map_123"
    assert mapping_repo.created_payload.name == "IfcWall to Wall"


def test_create_mapping_rejects_invalid_payload(monkeypatch):
    client, _mapping_repo = _build_client_with_mapping_repo(monkeypatch)
    response = client.post(
        "/lexicon/mappings",
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
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "Lexicon mapping is not valid"
    assert any("Target term must be a property" in item for item in detail["errors"])


def test_list_mappings(monkeypatch):
    client, _mapping_repo = _build_client_with_mapping_repo(monkeypatch)
    response = client.get("/lexicon/mappings")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["mappings"][0]["id"] == "map_123"


def test_get_mapping(monkeypatch):
    client, _mapping_repo = _build_client_with_mapping_repo(monkeypatch)
    response = client.get("/lexicon/mappings/map_123")
    assert response.status_code == 200
    body = response.json()
    assert body["mapping"]["id"] == "map_123"
    assert body["mapping"]["name"] == "IfcWall to Wall"


def test_delete_mapping(monkeypatch):
    client, mapping_repo = _build_client_with_mapping_repo(monkeypatch)
    response = client.delete("/lexicon/mappings/map_123")
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "mapping_id": "map_123"}
    assert mapping_repo.deleted_id == "map_123"


def test_create_handshake(monkeypatch):
    client, handshake_repo = _build_client_with_handshake_repo(monkeypatch)
    response = client.post(
        "/lexicon/handshakes",
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
    assert response.status_code == 201
    body = response.json()
    assert body["handshake"]["id"] == "hs_123"
    assert handshake_repo.created["ontology_family"] == "fbo"
    assert body["handshake"]["selected_ontologies"][0]["ontology_family"] == "fbo"


def test_get_handshake(monkeypatch):
    client, _handshake_repo = _build_client_with_handshake_repo(monkeypatch)
    response = client.get("/lexicon/handshakes/hs_123")
    assert response.status_code == 200
    body = response.json()
    assert body["handshake"]["id"] == "hs_123"
    assert body["handshake"]["profile"]["rule_id"] == "wall_rule"


def test_accept_handshake_payload(monkeypatch):
    client, handshake_repo = _build_client_with_handshake_repo(monkeypatch)
    response = client.post(
        "/lexicon/handshakes/hs_123/accept",
        json={
            "accepted_payload": {
                "mapping_ids": ["map_123"],
                "targets": [
                    {
                        "target_class": "Wall",
                        "source_object_ref": "ifc_guid_123",
                        "mapping_ids": ["map_123"],
                        "property_paths": ["Combustible"],
                        "evidence": [],
                    }
                ],
            }
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["handshake"]["accepted_payload"]["mapping_ids"] == ["map_123"]
    assert handshake_repo.updated_payload.mapping_ids == ["map_123"]


def test_handshake_next_needed(monkeypatch):
    client, handshake_repo = _build_client_with_handshake_repo(monkeypatch)
    handshake_repo.handshake = handshake_repo.handshake.model_copy(
        update={
            "accepted_payload": LexiconHandshakeAcceptedPayload(
                mapping_ids=["map_123"],
                targets=[
                    {
                        "target_class": "Wall",
                        "source_object_ref": "ifc_guid_123",
                        "mapping_ids": ["map_123"],
                        "property_paths": [],
                        "evidence": [],
                    }
                ],
            )
        }
    )
    response = client.post("/lexicon/handshakes/hs_123/next-needed")
    assert response.status_code == 200
    body = response.json()
    assert body["handshake_id"] == "hs_123"
    assert body["complete"] is False
    assert body["remaining_target_classes"] == []
    assert body["remaining_properties"][0]["property_path"] == "Combustible"


def test_reconstruct_handshake_graph_uses_mappings(monkeypatch):
    client, handshake_repo = _build_client_with_handshake_repo(monkeypatch)
    handshake_repo.handshake = handshake_repo.handshake.model_copy(
        update={
            "accepted_payload": LexiconHandshakeAcceptedPayload(
                mapping_ids=["map_semantic"],
                targets=[
                    {
                        "target_class": "Wall",
                        "source_object_ref": "ifc_guid_123",
                        "mapping_ids": [],
                        "property_paths": ["Combustible"],
                        "evidence": [],
                    }
                ],
            )
        }
    )
    response = client.post(
        "/lexicon/handshakes/hs_123/reconstruction",
        json={"graph_id": "hs-graph"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["graph_uri"] == "https://firebim.org/graphs/lexicon/hs-graph"
    assert "ns1:Wall" in body["ttl"]
    assert "ns1:Combustible true" in body["ttl"]


def test_handshake_conformity_uses_profile_and_mappings(monkeypatch):
    client, handshake_repo = _build_client_with_handshake_repo(monkeypatch)
    handshake_repo.handshake = handshake_repo.handshake.model_copy(
        update={
            "accepted_payload": LexiconHandshakeAcceptedPayload(
                mapping_ids=["map_semantic"],
                targets=[
                    {
                        "target_class": "Wall",
                        "source_object_ref": "ifc_guid_123",
                        "mapping_ids": [],
                        "property_paths": ["Combustible"],
                        "evidence": [],
                    }
                ],
            )
        }
    )
    response = client.post(
        "/lexicon/handshakes/hs_123/conformity",
        json={"graph_id": "hs-conformity"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["semantic_ok"] is True
    assert body["profile_alignment_ok"] is True
    assert body["execution_ready"] is True
    assert body["missing_profile_target_classes"] == []
    assert body["missing_profile_properties"] == []
