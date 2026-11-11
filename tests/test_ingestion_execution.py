from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlmodel import Session, select

from tests import test_ingestion_admission
from tests.test_ingestion_admission import complete

admission_api = test_ingestion_admission.admission_api


def test_claim_redelivery_and_expired_attempt_are_fenced(admission_api, engine, session):
    from app.ingestion.execution import ExecutionStore, LeaseLost
    from app.models.models import IngestionAttempt

    _, _, response = complete(admission_api)
    job_id = response.json()["id"]
    clock = [datetime.now(UTC)]
    store = ExecutionStore(lambda: Session(engine), clock=lambda: clock[0])
    first = store.claim(job_id, 1)
    assert first.number == 1
    assert store.claim(job_id, 1) is None
    clock[0] += timedelta(seconds=61)
    second = store.claim(job_id, 1)
    assert second.number == 2 and second.fencing_token > first.fencing_token
    with pytest.raises(LeaseLost):
        store.checkpoint(first, "converted", {"artifacts": {}})
    store.checkpoint(second, "converted", {"artifacts": {}})
    session.expire_all()
    attempts = session.exec(select(IngestionAttempt).order_by(IngestionAttempt.number)).all()
    assert [attempt.status for attempt in attempts] == ["abandoned", "running"]


def test_outbox_keeps_broker_failure_and_only_dispatches_single_task(admission_api, engine, session):
    from app.ingestion.execution import ExecutionStore
    from app.models.models import IngestionOutbox
    from app.workers.ingestion_dispatcher import dispatch_pending

    _, _, response = complete(admission_api)
    job_id = response.json()["id"]
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)
    broker = Mock()
    broker.send_task.side_effect = ConnectionError("secret broker credentials must not be persisted")
    assert dispatch_pending(store, broker) == 0
    session.expire_all()
    event = session.exec(select(IngestionOutbox)).one()
    assert event.delivered_at is None and event.dispatch_attempts == 1
    assert "secret" not in event.last_error
    now += timedelta(seconds=61)
    broker.send_task.side_effect = None
    assert dispatch_pending(store, broker) == 1
    assert broker.send_task.call_args.args == ("neo_ingestion",)
    assert broker.send_task.call_args.kwargs["kwargs"] == {"job_id": job_id, "generation": 1}
    assert dispatch_pending(store, broker) == 0


@pytest.mark.parametrize("failure", ["lost_message", "claim", "objects", "early_delivery"])
@pytest.mark.parametrize("status", ["queued", "retry_wait"])
def test_delivered_unclaimed_job_is_eventually_redispatched(
    admission_api,
    engine,
    session,
    monkeypatch,
    failure,
    status,
):
    from app.ingestion.execution import ExecutionStore, ProcessingError
    from app.models.models import IngestionAttempt
    from app.workers.ingestion_dispatcher import dispatch_pending
    from app.workers.neo_ingestion import neo_ingestion

    _, _, response = complete(admission_api)
    job_id = response.json()["id"]
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)
    generation = 1
    if status == "retry_wait":
        first = store.claim(job_id, generation)
        store.fail(first, ProcessingError("temporary", "Storage unavailable", transient=True))
        generation = 2
        now += timedelta(seconds=6)
    broker = Mock()
    assert dispatch_pending(store, broker) == 1
    if failure == "early_delivery":
        behind = ExecutionStore(lambda: Session(engine), clock=lambda: now - timedelta(seconds=10))
        assert behind.claim(job_id, generation) is None
    elif failure != "lost_message":
        with monkeypatch.context() as patch:
            patch.setattr("app.models.database.engine", engine)
            if failure == "claim":
                patch.setattr("app.storage.ingestion_objects.IngestionObjects.for_worker", Mock())
                patch.setattr(ExecutionStore, "claim", Mock(side_effect=ConnectionError("DB unavailable")))
            else:
                patch.setattr(
                    "app.storage.ingestion_objects.IngestionObjects.for_worker",
                    Mock(side_effect=ConnectionError("Storage unavailable")),
                )
            with pytest.raises(ConnectionError):
                neo_ingestion.run(job_id, generation)
    assert dispatch_pending(store, broker) == 0
    now += timedelta(seconds=121)
    store.recover_expired()
    assert dispatch_pending(store, broker) == 1
    assert dispatch_pending(store, broker) == 0
    assert broker.send_task.call_count == 2
    assert broker.send_task.call_args.kwargs["kwargs"] == {"job_id": job_id, "generation": generation}
    claim = store.claim(job_id, generation)
    assert claim.number == generation
    assert store.claim(job_id, generation) is None
    now += timedelta(seconds=121)
    assert dispatch_pending(store, broker) == 0  # Running jobs are recovered by their lease, not redelivered.
    session.expire_all()
    assert len(session.exec(select(IngestionAttempt)).all()) == generation


def test_redelivery_deadline_grows_exponentially_and_is_capped(admission_api, engine):
    from app.ingestion.execution import ExecutionStore
    from app.workers.ingestion_dispatcher import dispatch_pending

    complete(admission_api)
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)
    broker = Mock()
    assert dispatch_pending(store, broker) == 1

    for delay in (120, 240, 480, 900):
        now += timedelta(seconds=delay - 1)
        assert dispatch_pending(store, broker) == 0
        now += timedelta(seconds=1)
        assert dispatch_pending(store, broker) == 1
    assert broker.send_task.call_count == 5


@pytest.mark.parametrize("operation", ["heartbeat", "checkpoint"])
def test_transient_database_errors_during_progress_are_retryable(
    admission_api,
    engine,
    session,
    tmp_path,
    monkeypatch,
    operation,
):
    from sqlalchemy.exc import OperationalError

    from app.ingestion.execution import ExecutionStore
    from app.ingestion.pipeline import IngestionPipeline
    from app.models.models import IngestionJob

    _, service, _, _ = admission_api
    _, _, response = complete(admission_api)
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)
    original = getattr(store, operation)
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OperationalError(
                "UPDATE ingestion_jobs", {}, ConnectionError("database unavailable"), connection_invalidated=True
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(store, operation, fail_once)
    pipeline = IngestionPipeline(store, service.objects, Mock(), tmp_path)

    def fail_in_progress(claim, directory, report):
        if operation == "heartbeat":
            store.heartbeat(claim)
        else:
            store.checkpoint(claim, "converted", {})
        raise AssertionError("Injected database failure was not raised")

    monkeypatch.setattr(pipeline, "_prepare", fail_in_progress)

    assert pipeline.run(response.json()["id"], 1) == {"status": "failed", "code": "database_unavailable"}
    session.expire_all()
    job = session.get(IngestionJob, response.json()["id"])
    assert job.status == "retry_wait" and job.retryable
    assert job.report["diagnostics"][0]["code"] == "database_unavailable"


def test_nontransient_operational_error_is_not_classified_as_retryable():
    from sqlalchemy.exc import OperationalError

    from app.ingestion.pipeline import _is_transient_operational_error

    error = OperationalError("SELECT", {}, ValueError("invalid SQL"))
    assert not _is_transient_operational_error(error)


def test_transient_database_error_classification():
    from unittest.mock import Mock

    from sqlalchemy.exc import OperationalError

    from app.ingestion.pipeline import _is_transient_operational_error

    # Driver-level connection failures (no SQL state)
    error = OperationalError("SELECT", {}, Exception("Connection refused"))
    assert _is_transient_operational_error(error)

    # SQLSTATE class 53
    error = OperationalError("SELECT", {}, Exception("Insufficient resources"))
    error.orig = Mock(sqlstate="53000")
    assert _is_transient_operational_error(error)

    # SQLSTATE 57P01, 57P02, 57P03
    for code in ["57P01", "57P02", "57P03"]:
        error = OperationalError("SELECT", {}, Exception("Server state"))
        error.orig = Mock(sqlstate=code)
        assert _is_transient_operational_error(error)

    # Permanent: 57014 (statement timeout)
    error = OperationalError("SELECT", {}, Exception("Statement timeout"))
    error.orig = Mock(sqlstate="57014")
    assert not _is_transient_operational_error(error)


def test_reconciliation_broker_failure_keeps_bounded_backoff(admission_api, engine, session):
    from app.ingestion.execution import ExecutionStore
    from app.models.models import IngestionOutbox
    from app.workers.ingestion_dispatcher import dispatch_pending

    complete(admission_api)
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)
    broker = Mock()
    assert dispatch_pending(store, broker) == 1
    now += timedelta(seconds=121)
    broker.send_task.side_effect = ConnectionError("Broker unavailable")
    assert dispatch_pending(store, broker) == 0
    assert dispatch_pending(store, broker) == 0
    session.expire_all()
    event = session.exec(select(IngestionOutbox)).one()
    assert event.dispatch_attempts == 2 and event.delivered_at is None
    now += timedelta(seconds=5)
    broker.send_task.side_effect = None
    assert dispatch_pending(store, broker) == 1
    assert dispatch_pending(store, broker) == 0


@pytest.mark.parametrize("operation", ["cancel", "delete_model"])
def test_reconciliation_does_not_revive_cancelled_or_deleted_jobs(admission_api, engine, test_user, operation):
    from app.ingestion.execution import ExecutionStore
    from app.workers.ingestion_dispatcher import dispatch_pending

    _, service, _, _ = admission_api
    _, _, response = complete(admission_api)
    job = response.json()
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)
    broker = Mock()
    assert dispatch_pending(store, broker) == 1
    getattr(service, operation)(job["id"] if operation == "cancel" else job["model_id"], test_user)
    now += timedelta(seconds=121)
    assert dispatch_pending(store, broker) == 0
    assert broker.send_task.call_count == 1


@pytest.mark.parametrize("operation", ["cancel", "delete_model"])
def test_cancellation_and_deletion_fence_all_mutations(admission_api, engine, session, test_user, operation):
    from app.ingestion.execution import ExecutionStore, LeaseLost, ProcessingError
    from app.models.models import IfcModel

    _, service, _, _ = admission_api
    _, _, response = complete(admission_api)
    job_id = response.json()["id"]
    store = ExecutionStore(lambda: Session(engine))
    claim = store.claim(job_id, 1)
    getattr(service, operation)(job_id if operation == "cancel" else claim.job.model_id, test_user)
    for mutate in (
        lambda: store.heartbeat(claim),
        lambda: store.checkpoint(claim, "converted", {}),
        lambda: store.fail(claim, ProcessingError("late", "late failure")),
        lambda: store.publish(claim, lambda *args: "not-allowed"),
    ):
        with pytest.raises(LeaseLost):
            mutate()
    assert store.claim(job_id, 1) is None
    session.expire_all()
    assert session.get(IfcModel, claim.job.model_id).latest_successful_graph_id is None


def test_transient_retries_are_durable_bounded_and_generation_bound(admission_api, engine, session):
    from app.ingestion.execution import ExecutionStore, ProcessingError
    from app.models.models import IngestionJob, IngestionOutbox

    _, _, response = complete(admission_api)
    job_id = response.json()["id"]
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)
    for number in range(1, 4):
        claim = store.claim(job_id, number)
        assert claim.number == number
        store.fail(claim, ProcessingError("storage_unavailable", "Temporary storage failure", transient=True))
        assert store.claim(job_id, number) is None
        if number < 3:
            assert store.claim(job_id, number + 1) is None
        now += timedelta(seconds=61)
    session.expire_all()
    job = session.get(IngestionJob, job_id)
    assert job.status == "failed" and job.retryable and job.attempt_count == 3
    assert len(session.exec(select(IngestionOutbox)).all()) == 3
    assert store.claim(job_id, 3) is None
    client = admission_api[0]
    payload = {"request_id": "87c2fa11-4ba8-46e3-a84a-c2f5d6344593", "expected_dispatch_generation": 3}
    response = client.post(f"/ifc-models/jobs/{job_id}/retry", json=payload)
    assert response.status_code == 202, response.text
    assert response.json()["max_attempts"] == 4
    assert client.post(f"/ifc-models/jobs/{job_id}/retry", json=payload).json()["dispatch_generation"] == 4
    fourth = store.claim(job_id, 4)
    assert fourth.number == 4
    store.fail(fourth, ProcessingError("storage_unavailable", "Temporary storage failure", transient=True))
    now += timedelta(seconds=61)
    assert store.claim(job_id, 4) is None
    assert store.claim(job_id, 5) is None
    session.expire_all()
    job = session.get(IngestionJob, job_id)
    assert job.status == "failed" and job.retryable and job.attempt_count == job.max_attempts == 4
    assert len(session.exec(select(IngestionOutbox)).all()) == 4


def test_dispatcher_recovers_worker_death_without_reviving_old_fence(admission_api, engine, session):
    from app.ingestion.execution import ExecutionStore, LeaseLost
    from app.models.models import IngestionJob

    _, _, response = complete(admission_api)
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)
    first = store.claim(response.json()["id"], 1)
    now += timedelta(seconds=61)
    store.recover_expired()
    store.recover_expired()
    with pytest.raises(LeaseLost):
        store.heartbeat(first)
    session.expire_all()
    job = session.get(IngestionJob, first.job.id)
    assert job.status == "retry_wait" and job.dispatch_generation == 2
    now += timedelta(seconds=6)
    assert store.claim(job.id, 2).number == 2


def test_registration_callback_rollback_and_atomic_success(admission_api, engine, session):
    from app.ingestion.execution import ExecutionStore
    from app.models.models import IfcModel, IngestionJob

    _, _, response = complete(admission_api)
    store = ExecutionStore(lambda: Session(engine))
    claim = store.claim(response.json()["id"], 1)

    def crash(db, model, job):
        model.latest_successful_graph_id = "partial"
        db.add(model)
        db.flush()
        raise RuntimeError("crash before commit")

    with pytest.raises(RuntimeError):
        store.publish(claim, crash)
    session.expire_all()
    assert session.get(IfcModel, claim.job.model_id).latest_successful_graph_id is None
    assert session.get(IngestionJob, claim.job.id).status == "running"
    assert store.publish(claim, lambda *args: "graph-independent-of-model") == "graph-independent-of-model"
    assert store.published(claim, "graph-independent-of-model")
    assert store.claim(claim.job.id, 1) is None


def test_only_one_new_processing_task_is_explicitly_registered():
    from app.workers.celery_app import celery_app

    assert "app.workers.neo_ingestion" in celery_app.conf.include
    celery_app.loader.import_default_modules()
    task = celery_app.tasks["neo_ingestion"]
    assert task.acks_late and task.reject_on_worker_lost and task.ignore_result
    assert [name for name in celery_app.tasks if name.startswith("neo_")] == ["neo_ingestion"]


def test_publication_cannot_commit_after_lease_expires(admission_api, engine, session):
    from app.ingestion.execution import ExecutionStore, LeaseLost
    from app.models.models import IfcModel, IngestionJob

    _, _, response = complete(admission_api)
    now = datetime.now(UTC)
    store = ExecutionStore(lambda: Session(engine), clock=lambda: now)
    claim = store.claim(response.json()["id"], 1)

    def slow_registration(db, model, job):
        nonlocal now
        now += timedelta(seconds=61)
        model.latest_successful_graph_id = "late-graph"
        db.add(model)
        db.flush()
        return "late-graph"

    with pytest.raises(LeaseLost):
        store.publish(claim, slow_registration)
    session.expire_all()
    assert session.get(IngestionJob, claim.job.id).status == "running"
    assert session.get(IfcModel, claim.job.model_id).latest_successful_graph_id is None
