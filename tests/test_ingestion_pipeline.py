import json
import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlmodel import Session

from app.ingestion.execution import ExecutionStore, ProcessingError
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.policy import MODULES, PRODUCERS
from app.ingestion.runtime import IsolatedRuntime
from app.models.models import IfcModel, IngestionJob
from tests import test_ingestion_admission
from tests.test_ingestion_admission import complete

admission_api = test_ingestion_admission.admission_api


@pytest.fixture
def captured_converter():
    """Replace only the native executable boundary; run real parsing/mapping."""
    runtime = IsolatedRuntime("unused")

    def convert(directory, heartbeat):
        heartbeat()
        for producer in PRODUCERS:
            shutil.copyfile("tests/fixtures/neo/semantic_ifcowl.ttl", directory / f"source_{producer}.ttl")
        completion = "\n".join(f"module {module}: success" for module in ("parse", *MODULES))
        (directory / "converter.log").write_text(completion)
        return {"wall_seconds": 0.1}

    runtime.convert = Mock(side_effect=convert)
    return runtime


def test_full_task_keeps_validated_artifacts_private_until_publication(
    admission_api, engine, session, tmp_path, captured_converter, monkeypatch
):
    from celery.backends.cache import CacheBackend

    from app.core.config import settings
    from app.workers.celery_app import celery_app
    from app.workers.neo_ingestion import neo_ingestion

    _, service, objects, _ = admission_api
    _, _, response = complete(admission_api)
    job_id = response.json()["id"]
    monkeypatch.setattr("app.models.database.engine", engine)
    storage_client = Mock(return_value=objects)
    monkeypatch.setattr("boto3.client", storage_client)
    monkeypatch.setattr(settings, "NEO_ATTEMPT_ROOT", str(tmp_path))
    monkeypatch.setattr(IsolatedRuntime, "convert", captured_converter.convert)
    monkeypatch.setattr(neo_ingestion, "_backend", CacheBackend(app=celery_app, backend="memory"))
    dispatch = Mock(side_effect=AssertionError("No stage task dispatch is allowed"))
    monkeypatch.setattr(celery_app, "send_task", dispatch)
    result = neo_ingestion.apply(kwargs={"job_id": job_id, "generation": 1}).get()
    assert result["status"] == "succeeded", result
    from app.models.models import PublishedGraph

    graph = session.get(PublishedGraph, result["graph_id"])
    assert graph.job_id == job_id and graph.id != graph.model_id
    assert graph.backend == "neo" and graph.status == "ready"
    destination = service.objects.read_verified(
        **{
            "key": graph.destination["key"],
            "size": graph.destination["bytes"],
            "sha256": graph.destination["sha256"],
        }
    )
    from rdflib import Graph

    assert len(Graph().parse(data=destination, format="turtle")) == 5
    dispatch.assert_not_called()
    assert storage_client.call_args.kwargs["config"].read_timeout == 5
    session.expire_all()
    job = session.get(IngestionJob, job_id)
    assert set(job.checkpoints) == {"converted", "mapped", "validated", "publication_ready"}
    assert job.report["summary"]["assertions"] == 5
    assert job.report["report_version"] == "neo-ingestion/2"
    assert session.get(IfcModel, job.model_id).latest_successful_graph_id == graph.id
    evidence = job.checkpoints["validated"]["artifacts"]["evidence.json"]
    assert (
        json.loads(service.objects.read_verified(evidence["key"], evidence["bytes"], evidence["sha256"]))[
            "evidence_version"
        ]
        == "neo-evidence/1"
    )
    assert not tuple(tmp_path.iterdir())
    assert neo_ingestion.apply(kwargs={"job_id": job_id, "generation": 1}).get() == {"status": "ignored"}


@pytest.mark.parametrize("fingerprint_changed", [False, True])
def test_retry_reuses_only_verified_conversion_checkpoint(
    admission_api,
    engine,
    session,
    tmp_path,
    captured_converter,
    fingerprint_changed,
):
    _, service, _, _ = admission_api
    _, _, response = complete(admission_api)
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)

    def unavailable(*args):
        raise ProcessingError("publication_storage", "Temporary storage failure", transient=True)

    pipeline = IngestionPipeline(store, service.objects, captured_converter, tmp_path, publisher=unavailable)
    job_id = response.json()["id"]
    assert pipeline.run(job_id, 1)["code"] == "publication_storage"
    if fingerprint_changed:
        session.expire_all()
        job = session.get(IngestionJob, job_id)
        job.checkpoints = {
            **job.checkpoints,
            "converted": {**job.checkpoints["converted"], "execution_fingerprint": "other-worker"},
        }
        session.add(job)
        session.commit()
    now += timedelta(seconds=61)
    assert pipeline.run(job_id, 2)["code"] == "publication_storage"
    assert captured_converter.convert.call_count == (2 if fingerprint_changed else 1)
    session.expire_all()
    job = session.get(IngestionJob, job_id)
    assert job.status == "retry_wait" and job.attempt_count == 2


@pytest.mark.parametrize("corruption", ["hash", "size", "missing"])
def test_corrupt_checkpoint_cannot_be_reused(admission_api, engine, session, tmp_path, captured_converter, corruption):
    _, service, objects, _ = admission_api
    _, _, response = complete(admission_api)
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)

    def unavailable(*args):
        raise ProcessingError("temporary", "Temporary storage failure", transient=True)

    pipeline = IngestionPipeline(store, service.objects, captured_converter, tmp_path, publisher=unavailable)
    job_id = response.json()["id"]
    pipeline.run(job_id, 1)
    session.expire_all()
    job = session.get(IngestionJob, job_id)
    reference = job.checkpoints["converted"]["artifacts"]["source_ifcowl.ttl"]
    if corruption == "missing":
        del objects.values[reference["key"]]
    else:
        objects.values[reference["key"]] = b"x" * (reference["bytes"] if corruption == "hash" else 1)
    now += timedelta(seconds=61)
    assert pipeline.run(job_id, 2) == {"status": "failed", "code": "checkpoint_integrity_mismatch"}
    assert captured_converter.convert.call_count == 1
    session.expire_all()
    job = session.get(IngestionJob, job_id)
    assert not job.retryable
    assert job.report["diagnostics"][-1]["code"] == "checkpoint_integrity_mismatch"


@pytest.mark.parametrize("corruption", ["revision", "runtime", "input"])
def test_job_binding_rejected_before_conversion(
    admission_api,
    engine,
    session,
    tmp_path,
    captured_converter,
    corruption,
):
    _, service, _, _ = admission_api
    _, _, response = complete(admission_api)
    job = session.get(IngestionJob, response.json()["id"])
    if corruption == "revision":
        job.revision_digest = "corrupt"
    elif corruption == "runtime":
        job.runtime_config = {**job.runtime_config, "worker": {"version": "changed"}}
    else:
        job.raw_sha256 = "0" * 64
    session.add(job)
    session.commit()
    pipeline = IngestionPipeline(ExecutionStore(lambda: Session(engine)), service.objects, captured_converter, tmp_path)
    assert pipeline.run(job.id, 1)["status"] == "failed"
    captured_converter.convert.assert_not_called()


def test_zero_exit_does_not_imply_required_producer_completion(
    admission_api,
    engine,
    session,
    tmp_path,
    captured_converter,
):
    _, service, _, _ = admission_api
    _, _, response = complete(admission_api)

    def incomplete(directory, heartbeat):
        (directory / "converter.log").write_text("module parse: success")
        return {}

    captured_converter.convert.side_effect = incomplete
    pipeline = IngestionPipeline(ExecutionStore(lambda: Session(engine)), service.objects, captured_converter, tmp_path)
    assert pipeline.run(response.json()["id"], 1) == {"status": "failed", "code": "producer_incomplete"}


def test_cancellation_inside_conversion_does_not_schedule_retry(
    admission_api, engine, session, tmp_path, captured_converter, test_user
):
    from sqlmodel import select

    from app.models.models import IngestionAttempt, IngestionOutbox

    _, service, _, _ = admission_api
    _, _, response = complete(admission_api)
    job_id = response.json()["id"]

    def cancel(directory, heartbeat):
        service.cancel(job_id, test_user)
        heartbeat()

    captured_converter.convert.side_effect = cancel
    pipeline = IngestionPipeline(ExecutionStore(lambda: Session(engine)), service.objects, captured_converter, tmp_path)
    assert pipeline.run(job_id, 1) == {"status": "superseded"}
    session.expire_all()
    assert session.get(IngestionJob, job_id).status == "cancelled"
    assert session.exec(select(IngestionAttempt)).one().status == "cancelled"
    assert len(session.exec(select(IngestionOutbox)).all()) == 1


def test_mapping_rejection_retains_diagnostic_and_never_calls_publisher(
    admission_api, engine, session, tmp_path, captured_converter
):
    _, service, _, _ = admission_api
    _, _, response = complete(admission_api)
    original = captured_converter.convert.side_effect

    def selected_unsupported(directory, heartbeat):
        result = original(directory, heartbeat)
        path = directory / "source_ifcowl.ttl"
        path.write_text(path.read_text().replace('express#hasString> "Room A"', 'express#hasLogical> "Room A"'))
        return result

    captured_converter.convert.side_effect = selected_unsupported
    publisher = Mock(side_effect=AssertionError("Invalid mapping must not be published"))
    pipeline = IngestionPipeline(
        ExecutionStore(lambda: Session(engine)), service.objects, captured_converter, tmp_path, publisher=publisher
    )
    result = pipeline.run(response.json()["id"], 1)
    session.expire_all()
    job = session.get(IngestionJob, response.json()["id"])
    assert result == {"status": "failed", "code": "mapping_rejected"}, job.report
    publisher.assert_not_called()
    assert any(item["code"] == "unsupported_source_value" for item in job.report["diagnostics"])
    assert "mapping-report.json" in job.checkpoints["mapped"]["artifacts"]


def test_task_timeout_is_durable_and_not_retried(admission_api, engine, session, tmp_path, captured_converter):
    from billiard.exceptions import SoftTimeLimitExceeded

    _, service, _, _ = admission_api
    _, _, response = complete(admission_api)
    captured_converter.convert.side_effect = SoftTimeLimitExceeded()
    pipeline = IngestionPipeline(ExecutionStore(lambda: Session(engine)), service.objects, captured_converter, tmp_path)
    assert pipeline.run(response.json()["id"], 1) == {"status": "failed", "code": "attempt_timeout"}
    session.expire_all()
    job = session.get(IngestionJob, response.json()["id"])
    assert not job.retryable and job.status == "failed"
    assert "failure.log" in job.checkpoints["failure_evidence"]["artifacts"]
