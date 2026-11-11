import json
from datetime import UTC, datetime

import pytest
from rdflib import Graph, URIRef

from app.api import graphs
from app.lexicon.ifc_mapping_evaluator import _identity
from app.models.models import IfcModel, IngestionJob, PublishedGraph
from app.storage.building_repository import default_building_graph_uri
from app.storage.graph_store import GraphStoreNotFoundError
from tests import test_ingestion_admission, test_ingestion_rdf
from tests.test_ingestion_admission import complete

admission_api = test_ingestion_admission.admission_api
published_revision = test_ingestion_rdf.published_revision


def height_rule():
    from tests.test_rule_elements_v2 import _end, _ttl

    return _ttl(
        {
            "id": "height",
            "entry": "start",
            "elements": [
                {"id": "start", "element_type": "start", "true_path": "check"},
                {
                    "id": "check",
                    "element_type": "decision",
                    "target_class": "https://example.org/Space",
                    "property_path": "https://example.org/clearHeight",
                    "operator": ">=",
                    "value": 2,
                    "true_path": "height-ok",
                    "false_path": "height-fail",
                    "unknown_path": "height-ask",
                },
                _end("height", "ok", "pass"),
                _end("height", "fail", "fail"),
                _end("height", "ask", "missing_data"),
            ],
        }
    )


class Buildings:
    def __init__(self):
        self.values = {}

    def fetch_graph(self, graph_id):
        if graph_id not in self.values:
            raise GraphStoreNotFoundError(graph_id)
        return default_building_graph_uri(graph_id), self.values[graph_id]

    def list_graphs(self):
        return [{"graph_uri": default_building_graph_uri(key)} for key in self.values]


@pytest.fixture
def ready_graph(admission_api, session, monkeypatch):  # noqa: F811
    client, admission, objects, revision = admission_api
    _, _, response = complete(admission_api)
    job = session.get(IngestionJob, response.json()["id"])
    destination = admission.objects.write_private(
        "ingestion/test/destination.ttl",
        b"""
        @prefix ex: <https://example.org/> .
        <https://a.example/room> a ex:Room ; ex:height 2.5 .
        <https://b.example/room> a ex:Room ; ex:height 1.5 .
    """,
    )
    ontology = admission.objects.write_private(
        "ingestion/test/ontology.ttl",
        b"""
        @prefix ex: <https://example.org/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:Room rdfs:subClassOf ex:Space .
        ex:height rdfs:subPropertyOf ex:clearHeight .
    """,
    )
    graph = PublishedGraph(
        id="neo_test",
        graph_uri=default_building_graph_uri("neo_test"),
        user_id=job.user_id,
        model_id=job.model_id,
        job_id=job.id,
        profile_id=job.profile_id,
        revision_id=job.revision_id,
        revision_digest=job.revision_digest,
        input_sha256=job.raw_sha256,
        runtime_fingerprint=job.runtime_fingerprint,
        execution_fingerprint="execution",
        fencing_token=1,
        attempt_number=1,
        triple_count=4,
        destination=destination,
        provenance={},
        manifest={},
        report={},
        ontology_artifacts=[
            {
                "pin": {
                    "family": "example",
                    "country": "int",
                    "version": "1.0",
                    "sha256": ontology["sha256"],
                    "canonical_country_uri": "https://example.org/",
                    "canonical_family_uri": "https://example.org/",
                },
                "artifact": ontology,
            }
        ],
    )
    session.add(graph)
    session.commit()
    buildings = Buildings()
    monkeypatch.setattr(graphs, "repo", buildings)
    from app.storage.ingestion_objects import IngestionObjects

    monkeypatch.setattr(IngestionObjects, "for_worker", classmethod(lambda cls: admission.objects))
    return client, graph, buildings, objects


def attach_display_index(ready_graph, session, admission_api, entries, *, validation_changes=None):
    from app.ingestion.display import bind_display_index, encode_display_index

    _, graph, _, _ = ready_graph
    objects = admission_api[1].objects
    index = {
        "index_version": "neo-display-index/1",
        "model_id": graph.model_id,
        "revision_digest": graph.revision_digest,
        "entities": entries,
    }
    report = {"valid": True, "model_id": graph.model_id, "revision_digest": graph.revision_digest}
    index, report = bind_display_index(index, report)
    if validation_changes:
        report.update(validation_changes)
    index_ref = objects.write_private("ingestion/test/display-index.json", encode_display_index(index))
    report_ref = objects.write_private(
        "ingestion/test/validation-report.json",
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode(),
    )
    manifest = {
        "manifest_version": "neo-publication/1",
        "graph_id": graph.id,
        "model_id": graph.model_id,
        "job_id": graph.job_id,
        "revision_digest": graph.revision_digest,
        "artifacts": {"display-index.json": index_ref, "validation-report.json": report_ref},
    }
    graph.manifest = objects.write_private(
        "ingestion/test/manifest.json",
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
    )
    session.add(graph)
    session.commit()
    return index_ref


def test_ready_graph_is_read_from_verified_private_destination(ready_graph):
    client, graph, _, _ = ready_graph
    response = client.get(f"/graphs/{graph.id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["backend"] == "neo" and data["revision_id"] == graph.revision_id
    assert data["total_triples"] == 4 and not data["truncated"]
    assert {node["id"] for node in data["nodes"]} >= {
        "https://a.example/room",
        "https://b.example/room",
    }
    assert {node["id"]: node["label"] for node in data["nodes"] if node["kind"] == "Resource"} == {
        "https://a.example/room": "https://a.example/room",
        "https://b.example/room": "https://b.example/room",
        "https://example.org/Room": "https://example.org/Room",
    }
    literals = [node for node in data["nodes"] if node["kind"] == "Literal"]
    assert len(literals) == 2
    assert {(node["label"], node["metadata"]["datatype"]) for node in literals} == {
        ("1.5", "http://www.w3.org/2001/XMLSchema#decimal"),
        ("2.5", "http://www.w3.org/2001/XMLSchema#decimal"),
    }
    assert len({node["id"] for node in literals}) == 2
    assert all(node["id"].startswith('"literal:') and node["metadata"]["iri"] is None for node in literals)
    parsed = Graph().parse(data=data["ttl"], format="turtle")
    assert len(parsed) == 4
    assert not list(parsed.triples((None, URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf"), None)))


def test_preview_loads_revision_bound_names_and_retained_ontology_labels(ready_graph, session, admission_api):
    client, graph, _, _ = ready_graph
    objects = admission_api[1].objects
    wall = _identity(graph.model_id, "wall/one")
    door = _identity(graph.model_id, "door/two")
    graph.destination = objects.write_private(
        "ingestion/test/named-destination.ttl",
        f"""
        @prefix ex: <https://example.org/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <{wall}> a ex:Wall ; ex:value "https://example.org/Wall" ; rdfs:label "Destination wall"@en .
        <{door}> a ex:Door .
    """.encode(),
    )
    graph.triple_count = 4
    ontology_ttl = rb"""@prefix ex: <https://example.org/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:Wall rdfs:label "Wall without language" ; rdfs:label "Wall fran\u00e7ais"@fr ;
            rdfs:label "Wall English"@en ; rdfs:label "Wall English alternate"@en .
    """
    ontology_ref = objects.write_private("ingestion/test/named-ontology.ttl", ontology_ttl)
    pin = {**graph.ontology_artifacts[0]["pin"], "sha256": ontology_ref["sha256"]}
    graph.ontology_artifacts = [{"pin": pin, "artifact": ontology_ref}]
    attach_display_index(
        ready_graph,
        session,
        admission_api,
        {
            wall: {
                "ifc_class": "IfcWall",
                "global_id": "wall-guid",
                "name": "IFC wall name",
                "description": "Outer wall",
                "label_source": "Name",
            },
            door: {"ifc_class": "IfcDoor", "global_id": None, "name": None, "description": None, "label_source": None},
        },
    )
    session.add(graph)
    session.commit()

    response = client.get(f"/graphs/{graph.id}")

    assert response.status_code == 200, response.text
    nodes = {node["id"]: node for node in response.json()["nodes"]}
    assert nodes[wall]["label"] == "Destination wall"
    assert nodes[wall]["metadata"]["ifc_class"] == "IfcWall"
    assert nodes[wall]["metadata"]["global_id"] == "wall-guid"
    assert nodes[wall]["metadata"]["description"] == "Outer wall"
    assert nodes[wall]["metadata"]["label_source"] == "destination:rdfs:label"
    assert nodes["https://example.org/Wall"]["label"] == "Wall English"
    assert nodes["https://example.org/Wall"]["metadata"]["label_source"] == "ontology:rdfs:label"
    assert nodes[door]["label"] == "IfcDoor · door/two"
    assert nodes[door]["metadata"]["label_source"] == "ifc_class"
    literal = next(
        node for node in nodes.values() if node["kind"] == "Literal" and node["label"] == "https://example.org/Wall"
    )
    assert literal["label"] == "https://example.org/Wall"
    assert literal["id"].startswith('"literal:')
    assert literal["metadata"]["iri"] is None
    assert len(Graph().parse(data=response.json()["ttl"], format="nt")) == graph.triple_count


def test_preview_rejects_an_advertised_display_index_with_mismatched_validation(ready_graph, session, admission_api):
    client, graph, _, _ = ready_graph
    entity = _identity(graph.model_id, "wall/one")
    attach_display_index(
        ready_graph,
        session,
        admission_api,
        {
            entity: {
                "ifc_class": "IfcWall",
                "global_id": None,
                "name": "Wall",
                "description": None,
                "label_source": "Name",
            },
        },
        validation_changes={"revision_digest": "another-revision"},
    )

    response = client.get(f"/graphs/{graph.id}")

    assert response.status_code == 503
    assert "display index" in response.json()["detail"].lower()


def test_rule_evaluation_does_not_load_or_require_display_index(ready_graph, monkeypatch):
    from app.api import checks
    from app.storage.graph_resolver import GraphResolver

    client, graph, _, _ = ready_graph
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    monkeypatch.setattr(GraphResolver, "preview", lambda *args: pytest.fail("Evaluation requested a graph preview"))

    response = client.post("/checks/run", json={"graph_id": graph.id, "rule_id": "height"})

    assert response.status_code == 200, response.text


@pytest.mark.parametrize("endpoint", ["run", "run-vertical", "run-horisontal", "run-diagnostic", "run-blind"])
def test_all_check_modes_use_full_graph_and_retained_context(ready_graph, monkeypatch, endpoint):
    from app.api import checks

    client, graph, _, _ = ready_graph
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    monkeypatch.setattr(checks, "merge_schema_graphs", lambda **kwargs: pytest.fail("Mutable ontology lookup"))
    monkeypatch.setattr(checks, "fetch_building_graph_ttl", lambda uri: pytest.fail("Neo routed to legacy storage"))
    # Even a one-triple visualization cannot change the rule dataset.
    monkeypatch.setattr("app.storage.graph_resolver.PREVIEW_TRIPLES", 1)
    preview = client.get(f"/graphs/{graph.id}").json()
    assert preview["truncated"] and preview["preview_triples"] == 1
    response = client.post(f"/checks/{endpoint}", json={"graph_id": graph.id, "rule_id": "height"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"], body
    assert body["summary"]["total_targets"] == 2
    assert body["summary"]["passed"] == 1
    assert body["summary"]["failed"] == 1


@pytest.mark.parametrize("change", ["owner", "model_owner", "deleted", "unpublished", "backend"])
def test_private_graph_discovery_and_consumers_fail_closed(ready_graph, session, monkeypatch, change):
    from app.api import checks

    client, graph, buildings, _ = ready_graph
    model = session.get(IfcModel, graph.model_id)
    if change == "owner":
        graph.user_id = "another-user"
    elif change == "model_owner":
        model.user_id = "another-user"
    elif change == "deleted":
        model.deleted_at = datetime.now(UTC)
    elif change == "unpublished":
        graph.status = "preparing"
    else:
        graph.backend = "unknown"
    session.add(graph)
    session.add(model)
    session.commit()
    buildings.values[graph.id] = "<urn:secret> <urn:p> <urn:o> ."
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    assert graph.graph_uri not in client.get("/graphs").json()["graph_uris"]
    assert client.get(f"/graphs/{graph.id}").status_code == 404
    assert client.post("/graphs/extract-schema", json={"graph_id": graph.id}).status_code == 404
    for endpoint in ("run", "run-vertical", "run-horisontal", "run-diagnostic", "run-blind", "compatibility"):
        response = client.post(f"/checks/{endpoint}", json={"graph_id": graph.id, "rule_id": "height"})
        assert response.status_code == 404, (endpoint, response.text)
    response = client.post(
        "/lexicon/graphs/evidence-summary",
        json={
            "graph_id": graph.id,
            "selected_ontologies": [
                {"ontology_family": "example", "country": "int", "version": "1.0"},
            ],
        },
    )
    assert response.status_code == 404, response.text
    for path, body in (
        ("/lexicon/mappings/evaluate", {"mapping_id": "private-mapping"}),
        ("/lexicon/profiles/private-profile/graph-coverage", {}),
        ("/lexicon/profiles/derive", {"rule_id": "height", "ontology_family": "example", "country": "int"}),
    ):
        response = client.post(path, json={"graph_id": graph.id, **body})
        assert response.status_code == 404, response.text
    assert client.delete(f"/graphs/{graph.id}").status_code == 404


@pytest.mark.parametrize("canonical", [False, True])
def test_discovery_schema_compatibility_and_lexicon_select_same_revision(ready_graph, monkeypatch, canonical):
    from app.api import checks
    from app.storage.ontology_repository import OntologyRepository

    client, graph, _, _ = ready_graph
    selected = graph.graph_uri if canonical else graph.id
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    monkeypatch.setattr(OntologyRepository, "fetch_version", lambda *args: pytest.fail("Mutable ontology read"))
    items = client.get("/graphs").json()["graphs"]
    assert len(items) == 1 and items[0]["graph_id"] == graph.id
    assert items[0]["revision_digest"] == graph.revision_digest
    response = client.post("/graphs/extract-schema", json={"graph_id": selected})
    assert response.status_code == 200, response.text
    assert response.json()["graph_uri"] == graph.graph_uri
    assert response.json()["class_counts"]["https://example.org/Space"] == 2
    response = client.post("/checks/compatibility", json={"graph_id": selected, "rule_id": "height"})
    assert response.status_code == 200 and response.json()["compatibility_score"] == 1, response.text
    assert response.json()["target_classes_found"] == [{"value": "https://example.org/Space", "count": 2}]
    result = client.post("/checks/run", json={"graph_id": selected, "rule_id": "height"}).json()
    assert result["summary"]["total_targets"] == 2 and result["engine"]["merged_schemas"] == ["example/int/1.0"]
    response = client.post("/lexicon/graphs/evidence-summary", json={"graph_id": selected})
    assert response.status_code == 200, response.text
    evidence = response.json()
    assert evidence["graph_uri"] == graph.graph_uri and evidence["object_count"] == 2
    assert evidence["triple_count"] == 8
    assert {subject["source_object_ref"] for subject in evidence["subjects"]} == {
        "https://a.example/room",
        "https://b.example/room",
    }


@pytest.mark.parametrize("library_available", [True, False])
@pytest.mark.parametrize("requested_version", ["latest", "1.0"])
@pytest.mark.parametrize("explicit_selections", [True, False])
def test_neo_profile_derive_uses_retained_pins_after_library_changes(
    ready_graph,
    monkeypatch,
    library_available,
    requested_version,
    explicit_selections,
):
    from types import SimpleNamespace

    from app.storage.ontology_repository import OntologyRepository

    client, graph, _, _ = ready_graph
    metadata_reads = []

    def latest(*args):
        metadata_reads.append(args)
        if not library_available:
            raise GraphStoreNotFoundError("Retained pin no longer exists in the library")
        return SimpleNamespace(version="2.0")

    monkeypatch.setattr(OntologyRepository, "fetch_version_metadata", latest)
    monkeypatch.setattr(OntologyRepository, "fetch_version", lambda *args: pytest.fail("Mutable ontology read"))
    monkeypatch.setattr("app.lexicon.service.fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    payload = {
        "graph_id": graph.id,
        "rule_id": "height",
        "ontology_family": "example",
        "country": "int",
        "version": requested_version,
    }
    if explicit_selections:
        selection = {"ontology_family": "example", "country": "int", "version": requested_version}
        payload.update(selected_ontologies=[selection], canonical_target_ontology=selection)
        payload["graph_id"] = graph.graph_uri
    response = client.post("/lexicon/profiles/derive", json=payload)
    assert response.status_code == 200, response.text
    assert metadata_reads == []
    body = response.json()
    assert body["version"] == "1.0"
    assert body["selected_ontologies"][0]["version"] == "1.0"
    assert body["canonical_target_ontology"]["version"] == "1.0"
    assert body["profile"]["target_classes"] == ["https://example.org/Space"]
    assert body["profile"]["property_paths"] == ["https://example.org/clearHeight"]


def test_identity_collisions_never_route_neo_models_to_sql_or_legacy(ready_graph, session):
    client, graph, buildings, _ = ready_graph
    buildings.values[graph.model_id] = "<urn:wrong> <urn:p> <urn:o> ."
    buildings.values[graph.id] = "<urn:wrong> <urn:p> <urn:o> ."
    assert client.get(f"/graphs/{graph.model_id}").status_code == 404
    assert client.post("/graphs/extract-schema", json={"graph_id": "ifc://" + graph.model_id}).status_code == 404
    session.add(
        IfcModel(
            id=graph.id,
            filename="collision.ifc",
            s3_key="legacy/collision.ifc",
            user_id=graph.user_id,
            ingestion_backend="legacy",
        )
    )
    session.commit()
    response = client.get(f"/graphs/{graph.id}")
    assert response.status_code == 200 and response.json()["total_triples"] == 4
    items = client.get("/graphs").json()["graphs"]
    assert len(items) == 1 and items[0]["backend"] == "neo"


def test_legacy_detail_keeps_sql_precedence_without_hiding_either_graph(ready_graph, session, monkeypatch):
    from app.api import checks
    from app.models.models import IfcEntity

    client, graph, buildings, _ = ready_graph
    session.add(IfcModel(id="collision", filename="legacy.ifc", s3_key="legacy/collision.ifc", user_id=graph.user_id))
    session.commit()
    session.add(IfcEntity(global_id="sql-room", id="1", model_id="collision", type="IfcSpace", name="SQL room"))
    session.commit()
    ttl = "<urn:ttl-room> a <https://example.org/Space> ; <https://example.org/clearHeight> 3 ."
    buildings.values["collision"] = ttl
    monkeypatch.setattr("app.storage.building_repository.BuildingRepository.fetch_graph", buildings.fetch_graph)
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    monkeypatch.setattr(checks, "merge_schema_graphs", lambda **kwargs: (kwargs["graph_ttl"], []))

    response = client.get("/graphs/collision")
    assert response.status_code == 200, response.text
    assert response.json()["backend"] == "legacy-sql"
    assert response.json()["graph_uri"] == "ifc://collision"
    assert response.json()["ttl"] is None
    assert [node["id"] for node in response.json()["nodes"]] == ["sql-room"]
    response = client.get("/graphs/ifc://collision")
    assert response.status_code == 200, response.text
    assert response.json()["graph_id"] == "collision"
    assert response.json()["graph_uri"] == "ifc://collision"
    assert [node["id"] for node in response.json()["nodes"]] == ["sql-room"]
    items = [item for item in client.get("/graphs").json()["graphs"] if item["graph_id"] == "collision"]
    assert {item["graph_uri"] for item in items} == {"ifc://collision", default_building_graph_uri("collision")}
    response = client.post("/checks/run", json={"graph_id": "collision", "rule_id": "height"})
    assert response.status_code == 200 and response.json()["summary"]["passed"] == 1, response.text


def test_legacy_evidence_requires_nonempty_explicit_ontology_selections(ready_graph):
    client, _, buildings, _ = ready_graph
    buildings.values["legacy"] = "<urn:s> <urn:p> <urn:o> ."
    response = client.post("/lexicon/graphs/evidence-summary", json={"graph_id": "legacy", "selected_ontologies": []})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", "selected_ontologies"]


def test_legacy_lexicon_evidence_reuses_ontology_cache_across_requests(ready_graph, monkeypatch):
    from app.api import lexicon
    from app.lexicon.service import LexiconService
    from tests.test_lexicon_api import BUILDING_GRAPH_TTL, DummyOntologyRepo

    client, _, buildings, _ = ready_graph
    ontology_repo = DummyOntologyRepo()
    monkeypatch.setattr(lexicon, "service", LexiconService(ontology_repo=ontology_repo, building_repo=buildings))
    for graph_id in ("legacy-a", "legacy-b"):
        buildings.values[graph_id] = BUILDING_GRAPH_TTL.replace("wall-1", graph_id)
        response = client.post(
            "/lexicon/graphs/evidence-summary",
            json={
                "graph_id": graph_id,
                "selected_ontologies": [{"ontology_family": "fbo", "country": "dk", "version": "2026.04.23"}],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["graph_uri"] == default_building_graph_uri(graph_id)
        assert "https://example.org/buildings/" + graph_id in {
            subject["source_object_ref"] for subject in response.json()["subjects"]
        }
    assert ontology_repo.fetch_calls == 1


def test_neo_lexicon_does_not_reuse_library_bundle_with_same_pin_identity(ready_graph, monkeypatch):
    from app.api import lexicon
    from app.lexicon.service import LexiconService
    from tests.test_lexicon_api import DummyOntologyRepo

    client, graph, buildings, _ = ready_graph
    ontology_repo = DummyOntologyRepo()
    monkeypatch.setattr(lexicon, "service", LexiconService(ontology_repo=ontology_repo, building_repo=buildings))
    buildings.values["legacy"] = "<urn:legacy> a <https://example.org/Room> ."
    response = client.post(
        "/lexicon/graphs/evidence-summary",
        json={
            "graph_id": "legacy",
            "selected_ontologies": [{"ontology_family": "example", "country": "int", "version": "1.0"}],
        },
    )
    assert response.status_code == 200, response.text
    assert ontology_repo.fetch_calls == 1
    response = client.post("/lexicon/graphs/evidence-summary", json={"graph_id": graph.id})
    assert response.status_code == 200, response.text
    assert response.json()["class_term_ids"] == ["Space"]
    assert response.json()["object_count"] == 2
    assert ontology_repo.fetch_calls == 1


def test_unpublished_and_stale_artifacts_never_fall_back_to_legacy(ready_graph, monkeypatch):
    from app.api import checks

    client, _, buildings, _ = ready_graph
    buildings.values["neo_unpublished"] = "<urn:secret> <urn:p> <urn:o> ."
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    assert client.get("/graphs/neo_unpublished").status_code == 404
    assert client.post("/checks/run", json={"graph_id": "neo_unpublished", "rule_id": "height"}).status_code == 404
    assert "neo_unpublished" not in str(client.get("/graphs").json())


@pytest.mark.parametrize("artifact", ["destination", "ontology"])
def test_corrupt_private_artifacts_fail_verified_reads_without_disclosing_keys(ready_graph, artifact):
    client, graph, _, objects = ready_graph
    ref = graph.destination if artifact == "destination" else graph.ontology_artifacts[0]["artifact"]
    objects.values[ref["key"]] = b"corrupt"
    response = client.get(f"/graphs/{graph.id}")
    assert response.status_code == 503
    assert ref["key"] not in response.text


def test_immutable_graphs_cannot_be_written_answered_deleted_or_overlaid(ready_graph, monkeypatch):
    from app.api import checks

    client, graph, _, _ = ready_graph
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    response = client.post("/graphs/publish", json={"graph_id": graph.id, "ttl": "<urn:a> <urn:b> <urn:c> ."})
    assert response.status_code == 409
    answer = {"assertions": [{"subject": "urn:room", "property": "urn:height", "value": 5}]}
    assert client.post(f"/graphs/{graph.id}/answers", json=answer).status_code == 409
    assert client.post("/graphs/legacy/answers", json={**answer, "answered_graph_id": graph.id}).status_code == 409
    assert client.delete(f"/graphs/{graph.id}").status_code == 409
    for options in ({"persist_derived": True}, {"use_derived_from": ["other"]}, {"schema": "none"}):
        response = client.post("/checks/run", json={"graph_id": graph.id, "rule_id": "height", "options": options})
        assert response.status_code == 409, response.text
    assert client.get(f"/graphs/{graph.id}").json()["total_triples"] == 4


def test_reprocessing_pointer_does_not_retarget_selected_graph(ready_graph, session):
    client, graph, _, _ = ready_graph
    model = session.get(IfcModel, graph.model_id)
    model.latest_successful_graph_id = "neo_new_revision"
    session.add(model)
    session.commit()
    assert client.get(f"/graphs/{graph.id}").json()["revision_digest"] == graph.revision_digest
    assert client.get("/graphs/neo_new_revision").status_code == 404


def test_empty_and_byte_bounded_previews_do_not_invent_nodes(ready_graph, session, monkeypatch):
    from app.storage.ingestion_objects import IngestionObjects

    client, graph, _, _ = ready_graph
    monkeypatch.setattr("app.storage.graph_resolver.PREVIEW_BYTES", 1)
    preview = client.get(f"/graphs/{graph.id}").json()
    assert preview["nodes"] == [] and preview["edges"] == [] and preview["truncated"]
    assert preview["total_triples"] == 4
    objects = IngestionObjects.for_worker()
    graph.destination = objects.write_private("ingestion/test/empty.ttl", b"")
    graph.triple_count = 0
    session.add(graph)
    session.commit()
    preview = client.get(f"/graphs/{graph.id}").json()
    assert preview["total_triples"] == 0 and preview["nodes"] == [] and not preview["truncated"]


def test_live_model_deletion_revokes_graph_reports_and_source_download(ready_graph, session):
    client, graph, _, _ = ready_graph
    assert client.get(f"/ifc-models/jobs/{graph.job_id}/report").status_code == 200
    assert client.get(f"/ifc-models/{graph.model_id}/download").status_code == 200
    assert client.delete(f"/ifc-models/{graph.model_id}").status_code == 204
    assert session.get(PublishedGraph, graph.id).status == "ready"
    for path in (
        f"/graphs/{graph.id}",
        f"/ifc-models/jobs/{graph.job_id}/report",
        f"/ifc-models/{graph.model_id}/download",
    ):
        assert client.get(path).status_code == 404


def test_other_user_cannot_discover_read_or_delete_graph_evidence(ready_graph, session):
    from app.auth.jwt import get_current_active_user
    from app.main import app
    from app.models.models import User

    client, graph, _, _ = ready_graph
    other = User(authentik_sub="other", email="other@example.org", username="other")
    session.add(other)
    session.commit()
    app.dependency_overrides[get_current_active_user] = lambda: other
    assert client.get("/graphs").json()["count"] == 0
    assert client.get(f"/graphs/{graph.id}").status_code == 404
    assert client.get(f"/ifc-models/jobs/{graph.job_id}/report").status_code == 404
    assert client.get(f"/ifc-models/{graph.model_id}/download").status_code == 403
    assert client.delete(f"/ifc-models/{graph.model_id}").status_code == 403
    assert client.delete(f"/graphs/{graph.id}").status_code == 404


def test_anonymous_graph_consumers_require_authentication(ready_graph):
    from app.auth.jwt import get_current_active_user
    from app.main import app

    client, graph, _, _ = ready_graph
    del app.dependency_overrides[get_current_active_user]
    for method, path, body in (
        ("GET", "/graphs", None),
        ("GET", f"/graphs/{graph.id}", None),
        ("POST", "/graphs/extract-schema", {"graph_id": graph.id}),
        ("POST", "/checks/run", {"graph_id": graph.id, "rule_id": "height"}),
        ("POST", "/checks/run-blind", {"graph_id": graph.id, "rule_id": "height"}),
        ("POST", "/checks/compatibility", {"graph_id": graph.id, "rule_id": "height"}),
        ("POST", "/lexicon/graphs/evidence-summary", {"graph_id": graph.id}),
        ("DELETE", f"/graphs/{graph.id}", None),
    ):
        assert client.request(method, path, json=body).status_code == 401


def test_retained_pin_and_artifact_must_have_the_same_digest(ready_graph, session):
    client, graph, _, _ = ready_graph
    graph.ontology_artifacts = [
        {
            **graph.ontology_artifacts[0],
            "pin": {
                **graph.ontology_artifacts[0]["pin"],
                "sha256": "0" * 64,
            },
        }
    ]
    session.add(graph)
    session.commit()
    assert client.get(f"/graphs/{graph.id}").status_code == 503


def test_evaluation_concurrency_is_bounded_and_slots_are_released(ready_graph, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.api import checks

    client, graph, _, _ = ready_graph
    entered, release = Event(), Event()

    def rule(rule_id):
        entered.set()
        assert release.wait(5), "test did not release the evaluator"
        return "urn:height", height_rule()

    monkeypatch.setattr(checks, "fetch_rule_ttl", rule)
    payload = {"graph_id": graph.id, "rule_id": "height"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, "/checks/run", json=payload)
        try:
            assert entered.wait(5)
            response = client.post("/checks/compatibility", json=payload)
            assert response.status_code == 503 and response.headers["Retry-After"] == "1"
        finally:
            release.set()
        assert first.result(timeout=5).json()["summary"]["total_targets"] == 2
    assert client.post("/checks/run", json=payload).json()["summary"]["total_targets"] == 2


@pytest.mark.parametrize("endpoint", ["run", "compatibility"])
@pytest.mark.parametrize("request_kwargs", [{}, {"content": "not-json", "headers": {"Content-Type": "text/plain"}}])
def test_evaluation_limit_preserves_missing_body_validation(ready_graph, endpoint, request_kwargs):
    client, _, _, _ = ready_graph
    response = client.post(f"/checks/{endpoint}", **request_kwargs)
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body"]


@pytest.mark.parametrize("active_backend", ["legacy", "neo"])
def test_parallel_legacy_compatibility_and_previews_are_not_limited(ready_graph, monkeypatch, active_backend):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.api import checks

    client, graph, buildings, _ = ready_graph
    buildings.values["legacy"] = "<https://example.org/room> a <https://example.org/Space> ."
    monkeypatch.setattr("app.storage.building_repository.BuildingRepository.fetch_graph", buildings.fetch_graph)
    monkeypatch.setattr(checks, "merge_schema_graphs", lambda **kwargs: (kwargs["graph_ttl"], []))
    entered, release = Event(), Event()

    def rule(rule_id):
        if rule_id == "blocked":
            entered.set()
            assert release.wait(5), "test did not release the evaluator"
        return "urn:height", height_rule()

    monkeypatch.setattr(checks, "fetch_rule_ttl", rule)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(
            client.post,
            "/checks/compatibility",
            json={"graph_id": graph.id if active_backend == "neo" else "legacy", "rule_id": "blocked"},
        )
        try:
            assert entered.wait(5)
            response = client.post("/checks/compatibility", json={"graph_id": "legacy", "rule_id": "height"})
            assert response.status_code == 200, response.text
            assert response.json()["success"]
            assert client.get("/graphs/legacy").status_code == 200
            assert client.get(f"/graphs/{graph.id}").status_code == 200
        finally:
            release.set()
        assert first.result(timeout=5).json()["success"]


@pytest.mark.parametrize("graph_id", ["project/a", "project#phase", "project?phase"])
def test_legacy_ids_survive_discovery_reads_checks_and_writes(ready_graph, monkeypatch, graph_id):
    from urllib.parse import quote

    from app.api import checks
    from app.storage.building_repository import BuildingRepository
    from app.storage.graph_store import GraphStoreClient
    from tests.test_graph_store import FakeS3Client

    client, _, _, _ = ready_graph
    repo = BuildingRepository(GraphStoreClient(bucket_name="firebim", s3_client=FakeS3Client()))
    monkeypatch.setattr(graphs, "repo", repo)
    monkeypatch.setattr("app.storage.graph_resolver.BuildingRepository", lambda: repo)
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    monkeypatch.setattr(checks, "merge_schema_graphs", lambda **kwargs: (kwargs["graph_ttl"], []))
    ttl = "<https://example.org/room> a <https://example.org/Space> ; <https://example.org/clearHeight> 3 ."
    repo.publish_graph(graph_id, ttl)

    response = client.get("/graphs")
    assert response.status_code == 200, response.text
    assert default_building_graph_uri(graph_id) in response.json()["graph_uris"]
    path = "/graphs/" + quote(graph_id, safe="")
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["ttl"] == ttl
    for endpoint in ("run", "compatibility"):
        for selected in (graph_id, default_building_graph_uri(graph_id)):
            response = client.post(f"/checks/{endpoint}", json={"graph_id": selected, "rule_id": "height"})
            assert response.status_code == 200 and response.json()["success"], response.text
    response = client.post("/graphs/extract-schema", json={"graph_id": graph_id})
    assert response.status_code == 200, response.text
    response = client.post("/graphs/publish", json={"graph_id": graph_id, "ttl": ttl})
    assert response.status_code == 200, response.text
    answer = {"assertions": [{"subject": "https://example.org/room", "property": "urn:answer", "value": 5}]}
    response = client.post(path + "/answers", json=answer)
    assert response.status_code == 200, response.text
    assert response.json()["graph_id"] == graph_id + "--answered"
    response = client.delete(path)
    assert response.status_code == 200, response.text


def test_evaluator_does_not_retain_private_graphs_between_requests(ready_graph, monkeypatch):
    from app.api import checks
    from app.checks.caching import get_default_execution_cache

    client, graph, _, _ = ready_graph
    global_cache = get_default_execution_cache()
    caches = []

    def rule(rule_id):
        caches.append(get_default_execution_cache())
        return "urn:height", height_rule()

    monkeypatch.setattr(checks, "fetch_rule_ttl", rule)
    for _ in range(2):
        assert client.post("/checks/run", json={"graph_id": graph.id, "rule_id": "height"}).json()["success"]
    assert caches[0] is not caches[1] and global_cache not in caches
    assert all(graph.graph_uri not in key for key in global_cache.parsed_graphs)


def test_cache_entry_limits_and_inference_budget_are_enforced(ready_graph, monkeypatch):
    from dataclasses import replace

    from app.api import checks
    from app.checks import rdfs_light
    from app.checks.caching import CheckExecutionCache

    cache = CheckExecutionCache()
    for number in range(10):
        cache.get_or_parse_graph(graph_uri=f"urn:graph:{number}", ttl=f"<urn:a{number}> <urn:p> <urn:b> .")
    assert len(cache.parsed_graphs) == 2
    client, graph, _, _ = ready_graph
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:height", height_rule()))
    monkeypatch.setattr(rdfs_light, "LIMITS", replace(rdfs_light.LIMITS, triples=7))
    response = client.post("/checks/run", json={"graph_id": graph.id, "rule_id": "height"})
    assert response.status_code == 400 and "budget" in response.text


def test_low_level_derived_publisher_cannot_overwrite_reserved_baselines(monkeypatch):
    from app.graphs_and_checks import publisher

    writes = []
    monkeypatch.setattr(publisher, "_publish_graph_ttl", lambda **kwargs: writes.append(kwargs))
    for uri in (default_building_graph_uri("neo_test"), "https://firebim.org/graphs/derived/neo_test/rule"):
        with pytest.raises(ValueError, match="reserved"):
            publisher.publish_building_graph_ttl("<urn:a> <urn:p> <urn:b> .", uri)
    assert not writes
    publisher.publish_building_graph_ttl("<urn:a> <urn:p> <urn:b> .", default_building_graph_uri("legacy"))
    assert len(writes) == 1


@pytest.mark.parametrize("endpoint", ["run", "run-blind"])
def test_semantic_fixture_physical_units_survive_authorized_api_resolution(
    ready_graph,
    published_revision,
    session,
    monkeypatch,
    endpoint,  # noqa: F811
):
    from dataclasses import asdict

    from app.api import checks
    from app.ingestion.neo_revision import evaluate_neo_revision
    from app.ingestion.rdf import serialize_destination
    from app.storage.ingestion_objects import IngestionObjects
    from tests.test_ingestion_rdf import semantic_revision
    from tests.test_neo_ifcowl_adapter import EX, source_graph
    from tests.test_rule_elements_v2 import _end, _ttl

    client, graph, _, _ = ready_graph
    revision = semantic_revision(published_revision)
    _, evaluation = evaluate_neo_revision(revision, source_graph(), graph.model_id)
    serialized = serialize_destination(revision, asdict(evaluation), graph.model_id)
    objects = IngestionObjects.for_worker()
    graph.destination = objects.write_private("ingestion/test/semantic.ttl", serialized.destination)
    graph.triple_count = len(Graph().parse(data=serialized.destination, format="turtle"))
    graph.revision_id, graph.revision_digest = revision.revision_id, revision.content_digest
    graph.ontology_artifacts = [
        {
            "pin": artifact.pin.model_dump(mode="json"),
            "artifact": objects.write_private("ingestion/test/semantic-ontology.ttl", artifact.ttl.encode()),
        }
        for artifact in revision.content.ontology_artifacts
    ]
    session.add(graph)
    session.commit()
    rule = _ttl(
        {
            "id": "length",
            "entry": "start",
            "elements": [
                {"id": "start", "element_type": "start", "true_path": "check"},
                {
                    "id": "check",
                    "element_type": "decision",
                    "target_class": EX + "Wall",
                    "property_path": EX + "Length",
                    "operator": "<=",
                    "value": 1.3,
                    "true_path": "length-ok",
                    "false_path": "length-fail",
                    "unknown_path": "length-ask",
                },
                _end("length", "ok", "pass"),
                _end("length", "fail", "fail"),
                _end("length", "ask", "missing_data"),
            ],
        }
    )
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:length", rule))
    targets = [f"urn:firebim:model:{graph.model_id}:entity:{number:022}" for number in (10, 11)]
    response = client.post(
        f"/checks/{endpoint}", json={"graph_id": graph.id, "rule_id": "length", "options": {"target_nodes": targets}}
    )
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["passed"] == 1
    assert response.json()["summary"]["failed"] == 1
    assert response.json()["summary"]["total_targets"] == 2
