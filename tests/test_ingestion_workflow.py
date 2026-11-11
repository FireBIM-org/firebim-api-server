"""Browser-facing API workflow; opt into the real pinned converter with NEO_TEST_BINARY."""

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest
from rdflib import RDF, Graph, URIRef
from sqlmodel import Session

from app.ingestion.execution import ExecutionStore
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.runtime import IsolatedRuntime
from app.lexicon.translation_publication import prepare_translation_revision
from app.schemas.translation import TranslationProfileCreateRequest
from app.storage.ingestion_objects import IngestionObjects
from tests import test_ingestion_admission, test_ingestion_pipeline
from tests.test_graph_resolution import Buildings
from tests.test_ingestion_rdf import semantic_revision
from tests.test_neo_ifcowl_adapter import EX, mapping, read_target
from tests.test_rule_elements_v2 import _end, _ttl
from tests.test_translation_executable_mappings import Releases

admission_api = test_ingestion_admission.admission_api
captured_converter = test_ingestion_pipeline.captured_converter


def publish_workload(profiles, base, user, *, conflict=False):
    semantic = semantic_revision(base)
    semantic.content.compiled_mappings["area"] = mapping(
        read_target("CompartmentArea", datatype="double", pset="CPset_FireBIM", unit="ifc_area"),
        ifc_class="IfcSpace",
    )
    if conflict:
        semantic.content.compiled_mappings["external"].condition = None
    ttl = (
        semantic.content.ontology_artifacts[0].ttl
        + """
<https://example.org/fbo#> a owl:Ontology ; owl:versionIRI <https://example.org/fbo/2026.09.04#> .
d:CompartmentArea a owl:DatatypeProperty ; rdfs:range xsd:double .
"""
    )
    draft = profiles.create_profile(
        user=user,
        payload=TranslationProfileCreateRequest(
            kind="ifc_translation",
            name="Semantic workflow qualification",
            contract={
                "destination_modules": [{"family": "fbo", "country": "core"}],
                "mappings": [
                    {"id": key, "enabled": True, "review_state": "accepted", "definition": item.model_dump(mode="json")}
                    for key, item in semantic.content.compiled_mappings.items()
                ],
            },
        ),
    )
    return profiles.commit_revision(prepare_translation_revision(draft, Releases(ttl)), user)


def test_raw_upload_to_ready_rulebook_and_preserved_reprocessing(
    admission_api,
    engine,
    tmp_path,
    test_user,
    captured_converter,
    monkeypatch,
):
    from app.api import checks, graphs

    client, service, objects, base = admission_api
    revision = publish_workload(service.profiles, base, test_user)
    revisions = client.get("/ifc-models/ingestion-revisions").json()["revisions"]
    assert any(item["revision_id"] == revision.revision_id for item in revisions)
    source = Path("tests/fixtures/neo/semantic.ifc").read_bytes()
    payload = {
        "request_id": str(uuid.uuid4()),
        "filename": "semantic.ifc",
        "size_bytes": len(source),
        "sha256": hashlib.sha256(source).hexdigest(),
        "profile_id": revision.content.profile_id,
        "revision_id": revision.revision_id,
    }
    upload = client.post("/ifc-models/uploads", json=payload)
    assert upload.status_code == 201, upload.text
    upload = upload.json()
    objects.values[upload["upload_url"].removeprefix("https://private-storage.test/")] = source
    response = client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={})
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "queued" and job["result_graph_id"] is None
    monkeypatch.setattr(IngestionObjects, "for_worker", classmethod(lambda cls: service.objects))
    monkeypatch.setattr(graphs, "repo", Buildings())
    assert client.get(f"/graphs/{job['model_id']}").status_code == 404
    binary = os.environ.get("NEO_TEST_BINARY")
    runtime = IsolatedRuntime(binary) if binary else captured_converter
    store = ExecutionStore(lambda: Session(engine))
    pipeline = IngestionPipeline(store, service.objects, runtime, tmp_path)
    result = pipeline.run(job["id"], 1)
    assert result["status"] == "succeeded", result
    ready = client.get(f"/ifc-models/jobs/{job['id']}").json()
    graph_id = ready["result_graph_id"]
    assert graph_id != job["model_id"] and ready["status"] == "succeeded"
    model = client.get(f"/ifc-models/{job['model_id']}").json()
    assert model["latest_successful_graph_id"] == graph_id
    monkeypatch.setattr("app.storage.graph_resolver.PREVIEW_TRIPLES", 1)
    preview = client.get(f"/graphs/{graph_id}").json()
    assert preview["backend"] == "neo" and preview["immutable"]
    assert preview["truncated"] and preview["preview_triples"] == 1
    rule = _ttl(
        {
            "id": "length",
            "entry": "start",
            "elements": [
                {"id": "start", "element_type": "start", "true_path": "length-check"},
                {
                    "id": "length-check",
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
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:rule:length", rule))
    compatibility = client.post("/checks/compatibility", json={"graph_id": graph_id, "rule_id": "length"})
    assert compatibility.status_code == 200, compatibility.text
    checked = client.post("/checks/run-diagnostic", json={"graph_id": graph_id, "rule_id": "length"})
    assert checked.status_code == 200, checked.text
    outcomes = {item["target_node"].rsplit(":", 1)[-1]: item["status"] for item in checked.json()["targets"]}
    assert outcomes["0000000000000000000010"] == "True"
    assert outcomes["0000000000000000000011"] == "False"
    assert checked.json()["summary"]["total_targets"] == 5

    # The preview is not the dataset used for checks or the retained graph baseline.
    monkeypatch.setattr("app.storage.graph_resolver.PREVIEW_TRIPLES", 1000)
    baseline = client.get(f"/graphs/{graph_id}").json()["ttl"]
    graph = Graph().parse(data=baseline, format="turtle")
    wall = URIRef(f"urn:firebim:model:{job['model_id']}:entity:0000000000000000000010")
    assert float(graph.value(wall, URIRef(EX + "Length"))) == 1.2
    assert list(graph.objects(wall, URIRef(EX + "IsExternal")))[0].toPython() is True
    assert {float(value) for value in graph.objects(None, URIRef(EX + "CompartmentArea"))} == {25.0}
    assert len(tuple(graph.triples((None, URIRef(EX + "contains"), None)))) == 6
    report = client.get(f"/ifc-models/jobs/{job['id']}/report").json()
    assert report["attempts"][0]["status"] == "succeeded"

    def reprocess(selected):
        response = client.post(
            f"/ifc-models/{job['model_id']}/reprocess",
            json={
                "request_id": str(uuid.uuid4()),
                "profile_id": selected.content.profile_id,
                "revision_id": selected.revision_id,
            },
        )
        assert response.status_code == 202, response.text
        return response.json()

    second = reprocess(revision)
    assert client.get(f"/graphs/{graph_id}").json()["ttl"] == baseline
    result = pipeline.run(second["id"], 1)
    assert result["status"] == "succeeded", result
    assert result["graph_id"] != graph_id
    conflicting = publish_workload(service.profiles, base, test_user, conflict=True)
    third = reprocess(conflicting)
    assert pipeline.run(third["id"], 1)["status"] == "failed"
    failed = client.get(f"/ifc-models/jobs/{third['id']}").json()
    assert failed["status"] == "failed" and not failed["retryable"] and failed["result_graph_id"] is None
    assert client.get(f"/ifc-models/{job['model_id']}").json()["latest_successful_graph_id"] == result["graph_id"]
    assert client.get(f"/graphs/{graph_id}").json()["ttl"] == baseline
    history = client.get(f"/ifc-models/{job['model_id']}/jobs").json()
    assert len(history) == 3
    assert sum(item["status"] == "succeeded" for item in history) == 2

    if output := os.environ.get("NEO_TEST_REPORT"):
        # Private qualification evidence, intentionally separate from source-controlled docs.
        destination = Path(output).resolve()
        assert destination.is_relative_to(Path.cwd())
        destination.parent.mkdir(parents=True, exist_ok=True)
        from app.models.models import IngestionJob

        with Session(engine) as db:
            checkpoints = db.get(IngestionJob, job["id"]).checkpoints
        destination.write_text(
            json.dumps(
                {
                    "native": bool(binary),
                    "revision": revision.model_dump(mode="json"),
                    "input_bytes": len(source),
                    "job": ready,
                    "report": report,
                    "metrics": {key: value.get("metrics", {}) for key, value in checkpoints.items()},
                    "check_summary": checked.json()["summary"],
                },
                indent=2,
            )
        )
        destination.with_suffix(".revision.json").write_text(revision.model_dump_json())


def publish_class_only(profiles, base, user):
    """Class-only published revision: no relationship mappings, so containment stays out of scope."""
    semantic = semantic_revision(base)
    wall = semantic.content.compiled_mappings["wall"]
    ttl = semantic.content.ontology_artifacts[0].ttl + (
        "\n<https://example.org/fbo#> a owl:Ontology ; owl:versionIRI <https://example.org/fbo/2026.09.04#> .\n"
    )
    draft = profiles.create_profile(
        user=user,
        payload=TranslationProfileCreateRequest(
            kind="ifc_translation",
            name="Class-only architecture qualification",
            contract={
                "destination_modules": [{"family": "fbo", "country": "core"}],
                "mappings": [
                    {
                        "id": "wall",
                        "enabled": True,
                        "review_state": "accepted",
                        "definition": wall.model_dump(mode="json"),
                    }
                ],
            },
        ),
    )
    return profiles.commit_revision(prepare_translation_revision(draft, Releases(ttl)), user)


def test_architecture_ifc_with_class_only_revision_is_ready_and_checkable(
    admission_api,
    engine,
    tmp_path,
    test_user,
    monkeypatch,
):
    """The architecture sample passes class-only and fails closed with containment mappings."""
    import time

    from app.api import checks, graphs

    binary = os.environ.get("NEO_TEST_BINARY")
    if not binary:
        pytest.skip("NEO_TEST_BINARY not set")
    source_path = Path(__file__).resolve().parents[2] / "Building-Architecture.ifc"
    if not source_path.is_file():
        pytest.skip("optional Building-Architecture.ifc sample is not present")
    client, service, objects, base = admission_api
    from dataclasses import replace

    revision = publish_class_only(service.profiles, base, test_user)
    source = source_path.read_bytes()
    # Test-local allowance for this one 225,635-byte file; not a qualified capacity.
    service.runtime = replace(service.runtime, max_input_bytes=1024 * 1024)
    upload = client.post(
        "/ifc-models/uploads",
        json={
            "request_id": str(uuid.uuid4()),
            "filename": "Building-Architecture.ifc",
            "size_bytes": len(source),
            "sha256": hashlib.sha256(source).hexdigest(),
            "profile_id": revision.content.profile_id,
            "revision_id": revision.revision_id,
        },
    )
    assert upload.status_code == 201, upload.text
    upload = upload.json()
    objects.values[upload["upload_url"].removeprefix("https://private-storage.test/")] = source
    complete = client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={})
    assert complete.status_code == 202, complete.text
    job = complete.json()
    monkeypatch.setattr(IngestionObjects, "for_worker", classmethod(lambda cls: service.objects))
    monkeypatch.setattr(graphs, "repo", Buildings())
    pipeline = IngestionPipeline(
        ExecutionStore(lambda: Session(engine)), service.objects, IsolatedRuntime(binary), tmp_path
    )
    started = time.monotonic()
    result = pipeline.run(job["id"], 1)
    elapsed = time.monotonic() - started
    assert result["status"] == "succeeded", result
    graph_id = result["graph_id"]
    ready = client.get(f"/ifc-models/jobs/{job['id']}").json()
    assert ready["status"] == "succeeded" and ready["result_graph_id"] == graph_id
    assert client.get(f"/ifc-models/{job['model_id']}").json()["latest_successful_graph_id"] == graph_id
    monkeypatch.setattr("app.storage.graph_resolver.PREVIEW_TRIPLES", 100000)
    full = client.get(f"/graphs/{graph_id}").json()
    graph = Graph().parse(data=full["ttl"], format="turtle")
    walls = set(graph.subjects(RDF.type, URIRef(EX + "Wall")))
    assert len(walls) == 4
    assert all(str(wall).startswith(f"urn:firebim:model:{job['model_id']}:entity:") for wall in walls)
    assert not list(graph.triples((None, URIRef(EX + "contains"), None)))
    rule = _ttl(
        {
            "id": "walls",
            "entry": "start",
            "elements": [
                {"id": "start", "element_type": "start", "true_path": "present"},
                {
                    "id": "present",
                    "element_type": "decision",
                    "target_class": EX + "Wall",
                    "property_path": EX + "Length",
                    "operator": "<=",
                    "value": 1.0,
                    "true_path": "walls-ok",
                    "false_path": "walls-fail",
                    "unknown_path": "walls-ask",
                },
                _end("walls", "ok", "pass"),
                _end("walls", "fail", "fail"),
                _end("walls", "ask", "missing_data"),
            ],
        }
    )
    monkeypatch.setattr(checks, "fetch_rule_ttl", lambda rule_id: ("urn:rule:walls", rule))
    assert client.post("/checks/compatibility", json={"graph_id": graph_id, "rule_id": "walls"}).status_code == 200
    checked = client.post("/checks/run-diagnostic", json={"graph_id": graph_id, "rule_id": "walls"})
    assert checked.status_code == 200, checked.text
    assert checked.json()["summary"]["total_targets"] == 4

    richer_revision = publish_workload(service.profiles, base, test_user)
    reprocess = client.post(
        f"/ifc-models/{job['model_id']}/reprocess",
        json={
            "request_id": str(uuid.uuid4()),
            "profile_id": richer_revision.content.profile_id,
            "revision_id": richer_revision.revision_id,
        },
    )
    assert reprocess.status_code == 202, reprocess.text
    rejected_job = reprocess.json()
    rejected = pipeline.run(rejected_job["id"], 1)
    assert rejected == {"status": "failed", "code": "mapping_rejected"}
    rejection_report = client.get(f"/ifc-models/jobs/{rejected_job['id']}/report").json()
    assert any(item["code"] == "invalid_relationship_endpoint" for item in rejection_report["diagnostics"])
    assert client.get(f"/ifc-models/{job['model_id']}").json()["latest_successful_graph_id"] == graph_id

    with Session(engine) as db:
        from app.models.models import IngestionJob

        metrics = {key: value.get("metrics", {}) for key, value in db.get(IngestionJob, job["id"]).checkpoints.items()}
    if output := os.environ.get("NEO_ARCH_REPORT"):
        destination = Path(output).resolve()
        assert destination.is_relative_to(Path.cwd())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                {
                    "input_bytes": len(source),
                    "walls": len(walls),
                    "triples": len(graph),
                    "pipeline_seconds": round(elapsed, 3),
                    "metrics": metrics,
                },
                indent=2,
            )
        )
