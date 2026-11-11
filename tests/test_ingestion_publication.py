import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from urllib.parse import quote

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from app.ingestion.execution import ExecutionStore
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.publication import graph_id_for_job, publish_ingestion
from app.models.models import IfcModel, IngestionAttempt, IngestionJob, PublishedGraph
from app.storage.building_repository import BuildingRepository, default_building_graph_uri
from tests import test_ingestion_admission, test_ingestion_pipeline
from tests.test_ingestion_admission import complete

admission_api = test_ingestion_admission.admission_api
captured_converter = test_ingestion_pipeline.captured_converter


@pytest.fixture
def publication(admission_api, engine, tmp_path, captured_converter):
    _, service, objects, _ = admission_api
    _, _, response = complete(admission_api)
    now = [datetime.now(UTC)]
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now[0])
    pipeline = IngestionPipeline(store, service.objects, captured_converter, tmp_path)
    return pipeline, response.json()["id"], store, now, objects


def read(objects, reference):
    return objects.read_verified(reference["key"], reference["bytes"], reference["sha256"])


def test_publication_manifest_has_verified_private_artifacts_and_exact_ontologies(publication, admission_api, session):
    pipeline, job_id, _, _, objects = publication
    _, service, _, revision = admission_api
    result = pipeline.run(job_id, 1)
    if result["status"] != "succeeded":
        pytest.fail(
            str(result)
            + "\n"
            + "\n".join(
                value.decode(errors="replace")
                for key, value in objects.values.items()
                if key.endswith("/validation.log")
            )
        )
    graph = session.get(PublishedGraph, result["graph_id"])
    manifest = json.loads(read(service.objects, graph.manifest))
    assert manifest["raw_input"]["sha256"] == graph.input_sha256
    assert manifest["revision_digest"] == revision.content_digest
    assert manifest["graph_id"] == graph.id != graph.model_id
    assert manifest["execution_fingerprint"] == graph.execution_fingerprint
    assert manifest["validation"]["valid"]
    display_index = json.loads(read(service.objects, manifest["artifacts"]["display-index.json"]))
    validation_report = json.loads(read(service.objects, manifest["artifacts"]["validation-report.json"]))
    assert display_index["model_id"] == graph.model_id
    assert display_index["revision_digest"] == graph.revision_digest
    assert validation_report["display_index_sha256"] == manifest["artifacts"]["display-index.json"]["sha256"]
    for reference in manifest["artifacts"].values():
        assert reference["key"].startswith(f"ingestion/jobs/{job_id}/attempts/")
        read(service.objects, reference)
    assert read(service.objects, graph.ontology_artifacts[0]["artifact"]) == (
        revision.content.ontology_artifacts[0].ttl.encode()
    )
    assert json.loads(read(service.objects, graph.report))["destination"]["triples"] == 5
    assert not any(key.startswith("graphs/") for key in objects.values)
    session.expire_all()
    assert session.get(IngestionJob, job_id).status == "succeeded"
    assert session.exec(select(IngestionAttempt)).one().status == "succeeded"
    assert session.get(IfcModel, graph.model_id).latest_successful_job_id == job_id


@pytest.mark.parametrize(
    "artifact", ["destination.ttl", "provenance.ttl", "ontology-0.ttl", "publication-report.json", "manifest.json"]
)
def test_storage_failure_never_exposes_partial_graph(publication, admission_api, session, monkeypatch, artifact):
    pipeline, job_id, _, _, _ = publication
    _, service, _, _ = admission_api
    original = service.objects.write_private

    def fail(key, data):
        if key.endswith("/" + artifact):
            from app.ingestion.jobs import AdmissionError

            raise AdmissionError(503, "storage_unavailable", "Injected storage failure")
        return original(key, data)

    monkeypatch.setattr(service.objects, "write_private", fail)
    assert pipeline.run(job_id, 1) == {"status": "failed", "code": "storage_unavailable"}
    session.expire_all()
    assert not session.exec(select(PublishedGraph)).all()
    job = session.get(IngestionJob, job_id)
    assert job.status == "retry_wait" and job.result_graph_id is None
    assert session.get(IfcModel, job.model_id).latest_successful_graph_id is None


@pytest.mark.parametrize("moment", ["before_commit", "after_commit"])
def test_commit_failure_is_atomic_and_ambiguous_success_is_reconciled(publication, engine, session, moment):
    pipeline, job_id, _, _, _ = publication
    fired = []

    def crash(db):
        if fired or not db.info.get("publishing_graph"):
            return
        fired.append(True)
        raise RuntimeError("Injected crash " + moment)

    def mark(db, flush_context):
        if any(isinstance(item, PublishedGraph) for item in db.new):
            db.info["publishing_graph"] = True

    # after_flush sees newly inserted graph rows, including their transaction's success updates.
    event.listen(Session, "after_flush", mark)
    event.listen(Session, moment, crash)
    try:
        result = pipeline.run(job_id, 1)
    finally:
        event.remove(Session, moment, crash)
        event.remove(Session, "after_flush", mark)
    assert fired
    session.expire_all()
    job = session.get(IngestionJob, job_id)
    model = session.get(IfcModel, job.model_id)
    if moment == "before_commit":
        assert result == {"status": "failed", "code": "processing_failed"}
        assert not session.exec(select(PublishedGraph)).all()
        assert job.status == "failed" and model.latest_successful_graph_id is None
    else:
        assert result["status"] == "succeeded", result
        assert session.exec(select(PublishedGraph)).one().id == model.latest_successful_graph_id == job.result_graph_id
        assert job.status == "succeeded"


def test_publisher_replay_is_idempotent_and_writes_nothing_after_success(publication, tmp_path, session):
    pipeline, job_id, store, _, objects = publication
    claim = store.claim(job_id, 1, "qualified-execution")
    prepared = pipeline._prepare(claim, tmp_path, {})
    first = publish_ingestion(store, claim, prepared)
    puts = len(objects.puts)
    assert publish_ingestion(store, claim, prepared) == first
    assert len(objects.puts) == puts
    assert len(session.exec(select(PublishedGraph)).all()) == 1


@pytest.mark.parametrize("fence", ["cancel", "delete", "expire", "supersede"])
def test_fencing_between_private_writes_and_commit_cannot_resurrect_results(
    publication,
    admission_api,
    session,
    test_user,
    monkeypatch,
    fence,
):
    pipeline, job_id, store, now, _ = publication
    _, service, _, _ = admission_api
    original = store.publish

    def fenced(claim, register):
        if fence == "cancel":
            service.cancel(job_id, test_user)
        elif fence == "delete":
            service.delete_model(claim.job.model_id, test_user)
        elif fence == "expire":
            now[0] += timedelta(seconds=61)
        else:
            now[0] += timedelta(seconds=61)
            assert store.claim(job_id, 1).fencing_token > claim.fencing_token
        return original(claim, register)

    monkeypatch.setattr(store, "publish", fenced)
    assert pipeline.run(job_id, 1) == {"status": "superseded"}
    session.expire_all()
    assert not session.exec(select(PublishedGraph)).all()
    assert session.get(IfcModel, session.get(IngestionJob, job_id).model_id).latest_successful_graph_id is None


def test_crash_before_commit_can_resume_same_claim_without_overwriting_artifacts(publication, tmp_path, monkeypatch):
    pipeline, job_id, store, _, _ = publication
    claim = store.claim(job_id, 1, "execution")
    prepared = pipeline._prepare(claim, tmp_path, {})
    original = store.publish
    monkeypatch.setattr(store, "publish", Mock(side_effect=RuntimeError("Crash before registration")))
    with pytest.raises(RuntimeError):
        publish_ingestion(store, claim, prepared)
    monkeypatch.setattr(store, "publish", original)
    assert publish_ingestion(store, claim, prepared) == graph_id_for_job(job_id)


def test_reprocess_retains_previous_success_and_uses_a_new_graph_identity(publication, admission_api, session):
    pipeline, job_id, _, now, objects = publication
    client, _, _, revision = admission_api
    first = pipeline.run(job_id, 1)
    assert first["status"] == "succeeded", first
    first_graph = session.get(PublishedGraph, first["graph_id"])
    before = dict(objects.values)
    response = client.post(
        f"/ifc-models/{first_graph.model_id}/reprocess",
        json={
            "request_id": str(uuid.uuid4()),
            "profile_id": revision.content.profile_id,
            "revision_id": revision.revision_id,
        },
    )
    assert response.status_code == 202, response.text
    session.expire_all()
    assert session.get(IfcModel, first_graph.model_id).latest_successful_graph_id == first_graph.id
    now[0] = datetime.now(UTC)
    second = pipeline.run(response.json()["id"], 1)
    assert second["status"] == "succeeded", second
    assert second["graph_id"] != first_graph.id
    assert session.get(PublishedGraph, first_graph.id).destination == first_graph.destination
    session.expire_all()
    assert session.get(IfcModel, first_graph.model_id).latest_successful_graph_id == second["graph_id"]
    assert all(objects.values[key] == data for key, data in before.items())
    assert len(session.exec(select(PublishedGraph)).all()) == 2


@pytest.mark.parametrize("legacy", ["graph", "model"])
def test_graph_identity_cannot_shadow_legacy_ids(publication, session, legacy):
    pipeline, job_id, _, _, objects = publication
    graph_id = graph_id_for_job(job_id)
    if legacy == "graph":
        key = "graphs/buildings/" + quote(default_building_graph_uri(graph_id), safe="") + ".ttl"
        objects.values[key] = b"<urn:existing> a <urn:Legacy> ."
    else:
        session.add(IfcModel(id=graph_id, filename="legacy.ifc", s3_key="legacy/raw", user_id="legacy"))
        session.commit()
    assert pipeline.run(job_id, 1) == {"status": "failed", "code": "graph_identity_collision"}
    session.expire_all()
    assert not session.exec(select(PublishedGraph)).all()


@pytest.mark.parametrize("graph_id", ["neo_anything", default_building_graph_uri("neo_anything")])
def test_legacy_writes_cannot_claim_immutable_graph_namespace(graph_id):
    client = Mock()
    with pytest.raises(ValueError, match="reserved"):
        BuildingRepository(client).publish_graph(graph_id, "<urn:s> a <urn:Class> .")
    client.put_graph_ttl.assert_not_called()


def test_semantic_rejection_is_durable_and_keeps_destination_unpublished(publication, session, monkeypatch):
    pipeline, job_id, _, _, _ = publication
    original = pipeline.runtime.validate

    def invalid(directory, heartbeat):
        path = directory / "evaluation.json"
        evaluation = json.loads(path.read_text())
        evaluation["assertions"][0]["object"] = "urn:undeclared:Class"
        path.write_text(json.dumps(evaluation))
        return original(directory, heartbeat)

    monkeypatch.setattr(pipeline.runtime, "validate", invalid)
    pipeline.publisher = Mock(side_effect=AssertionError("Rejected semantics must not publish"))
    assert pipeline.run(job_id, 1) == {"status": "failed", "code": "semantic_rejection"}
    pipeline.publisher.assert_not_called()
    session.expire_all()
    job = session.get(IngestionJob, job_id)
    assert job.status == "failed" and not job.retryable
    assert job.report["diagnostics"][0]["code"] == "semantic_rejection"
    assert "validation-report.json" in job.checkpoints["failure_evidence"]["artifacts"]
    assert not session.exec(select(PublishedGraph)).all()


@pytest.mark.parametrize("name", ["destination.ttl", "provenance.ttl", "validation-report.json", "display-index.json"])
def test_corrupted_validated_artifact_cannot_publish(publication, tmp_path, session, name):
    from app.ingestion.jobs import AdmissionError

    pipeline, job_id, store, _, objects = publication
    claim = store.claim(job_id, 1, "execution")
    prepared = pipeline._prepare(claim, tmp_path, {})
    reference = prepared.artifacts[name]
    objects.values[reference["key"]] = b"X" * reference["bytes"]
    with pytest.raises(AdmissionError):
        publish_ingestion(store, claim, prepared)
    assert not session.exec(select(PublishedGraph)).all()
