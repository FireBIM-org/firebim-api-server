"""Exercise real PostgreSQL row locks in a disposable schema (no existing DDL)."""

import argparse
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import Mock, patch

from sqlalchemy import func, text
from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.execution import ExecutionStore, LeaseLost, ProcessingError
from app.models.models import IfcModel, IfcUploadSession, IngestionAttempt, IngestionJob, IngestionOutbox
from app.workers.ingestion_dispatcher import dispatch_pending


def check(database_url):
    started = time.monotonic()
    schema = "neo_check_" + uuid.uuid4().hex
    admin = create_engine(database_url)
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(database_url, connect_args={"options": f"-c search_path={schema} -c statement_timeout=5000"})
    try:
        SQLModel.metadata.create_all(engine)
        now = datetime.now(UTC)
        with Session(engine) as db:
            model = IfcModel(filename="probe.ifc", s3_key="private/raw", user_id="probe", ingestion_backend="neo")
            db.add(model)
            db.flush()
            upload = IfcUploadSession(
                user_id="probe",
                request_id="probe",
                request_digest="digest",
                model_id=model.id,
                filename="probe.ifc",
                staging_key="staging",
                expected_size=1,
                expected_sha256="hash",
                profile_id="profile",
                revision_id="revision",
                revision_digest="digest",
                revision_snapshot={},
                runtime_fingerprint="runtime",
                runtime_config={},
                expires_at=now + timedelta(minutes=10),
            )
            db.add(upload)
            db.flush()
            job = IngestionJob(
                user_id="probe",
                model_id=model.id,
                upload_session_id=upload.id,
                request_id="probe",
                request_digest="digest",
                raw_key="private/raw",
                raw_sha256="hash",
                raw_size=1,
                profile_id="profile",
                revision_id="revision",
                revision_digest="digest",
                revision_snapshot={},
                runtime_fingerprint="runtime",
                runtime_config={},
            )
            db.add(job)
            db.flush()
            db.add(IngestionOutbox(job_id=job.id))
            db.commit()
            job_id = job.id
        store = ExecutionStore(lambda: Session(engine), clock=lambda: datetime.now(UTC) - timedelta(days=1))
        ahead = ExecutionStore(lambda: Session(engine), clock=lambda: datetime.now(UTC) + timedelta(days=1))
        broker = Mock()
        barrier = Barrier(8)

        def dispatch(_):
            barrier.wait(timeout=5)
            return dispatch_pending(store, broker)

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(dispatch, range(8))) == 1
        assert broker.send_task.call_count == 1

        # Acknowledged/lost messages and exceptions before claim have no lease
        # to recover. Age only the durable delivery timestamp, never sleep 120s.
        with patch.dict(
            "os.environ",
            {
                "POSTGRES_HOST": "localhost",
                "POSTGRES_USER": "probe",
                "POSTGRES_PASSWORD": "probe",
                "S3_ENDPOINT_URL": "http://localhost:1",
                "S3_BUCKET_NAME": "probe",
                "S3_ACCESS_KEY_ID": "probe",
                "S3_SECRET_ACCESS_KEY": "probe",
                "S3_REGION": "us-east-1",
                "CELERY_BROKER_URL": "memory://",
                "CELERY_RESULT_BACKEND": "cache+memory://",
            },
        ):
            from app.workers.neo_ingestion import neo_ingestion

        for failure, redelivery_age in zip(("lost_message", "objects", "claim"), (121, 241, 481), strict=True):
            with patch("app.models.database.engine", engine):
                if failure == "objects":
                    with patch(
                        "app.storage.ingestion_objects.IngestionObjects.for_worker",
                        side_effect=ConnectionError("Storage unavailable"),
                    ):
                        try:
                            neo_ingestion.run(job_id, 1)
                        except ConnectionError:
                            pass
                        else:
                            raise AssertionError("Expected pre-claim storage failure")
                elif failure == "claim":
                    with (
                        patch("app.storage.ingestion_objects.IngestionObjects.for_worker"),
                        patch.object(ExecutionStore, "claim", side_effect=ConnectionError("DB unavailable")),
                    ):
                        try:
                            neo_ingestion.run(job_id, 1)
                        except ConnectionError:
                            pass
                        else:
                            raise AssertionError("Expected pre-claim database failure")
            with engine.begin() as db:
                db.execute(
                    text(
                        f"UPDATE ingestion_outbox SET delivered_at = "
                        f"clock_timestamp() - interval '{redelivery_age} seconds'"
                    )
                )
            barrier = Barrier(8)
            with ThreadPoolExecutor(max_workers=8) as pool:
                assert sum(pool.map(dispatch, range(8))) == 1
            assert dispatch_pending(ahead, broker) == 0  # Host clock cannot prematurely reconcile.
            with Session(engine) as db:
                assert db.exec(select(IngestionJob)).one().status == "queued"
                assert not db.exec(select(IngestionAttempt)).all()
        assert broker.send_task.call_count == 4
        barrier = Barrier(8)

        def claim(_):
            barrier.wait(timeout=5)
            return store.claim(job_id, 1)

        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = [value for value in pool.map(claim, range(8)) if value]
        assert len(claims) == 1
        active = claims[0]
        ahead.recover_expired()  # A dispatcher one day ahead cannot fence a live worker.
        assert ahead.claim(job_id, 1) is None
        store.heartbeat(active)
        ahead.checkpoint(active, "converted", {"artifacts": {}})
        with Session(engine) as db:
            now = db.exec(select(func.clock_timestamp())).one()
            job = db.get(IngestionJob, job_id)
            assert 55 < (job.lease_expires_at - now).total_seconds() <= 60
        store.fail(active, ProcessingError("temporary", "Storage unavailable", transient=True))
        assert dispatch_pending(ahead, broker) == 0  # DB retry delay holds despite host skew.
        assert ahead.claim(job_id, 2) is None
        with engine.begin() as db:
            db.execute(
                text(
                    "UPDATE ingestion_outbox SET available_at = clock_timestamp() - interval '1 second', "
                    "delivered_at = clock_timestamp() - interval '121 seconds' WHERE generation = 1"
                )
            )
        assert dispatch_pending(ahead, broker) == 0  # Do not resend an obsolete generation.
        with engine.begin() as db:
            db.execute(
                text(
                    "UPDATE ingestion_outbox SET available_at = clock_timestamp() - interval '1 second' "
                    "WHERE generation = 2"
                )
            )
        assert dispatch_pending(store, broker) == 1
        with engine.begin() as db:
            db.execute(
                text(
                    "UPDATE ingestion_outbox SET delivered_at = clock_timestamp() - interval '121 seconds' "
                    "WHERE generation = 2"
                )
            )
        barrier = Barrier(8)
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(dispatch, range(8))) == 1
        assert broker.send_task.call_args.kwargs["kwargs"] == {"job_id": job_id, "generation": 2}
        active = store.claim(job_id, 2)
        assert active.number == 2
        with engine.begin() as db:
            db.execute(text("UPDATE ingestion_jobs SET lease_expires_at = clock_timestamp() + interval '0.2 seconds'"))

        def slow_registration(db, model, job):
            db.exec(select(func.pg_sleep(0.3)))
            return "too-late"

        try:
            ahead.publish(active, slow_registration)
        except LeaseLost:
            pass
        else:
            raise AssertionError("Publication used transaction-start time instead of current DB time")
        ahead.recover_expired()
        with Session(engine) as db:
            job = db.get(IngestionJob, job_id)
            assert job.status == "retry_wait" and job.dispatch_generation == 3
        try:
            store.heartbeat(active)
        except LeaseLost:
            pass
        else:
            raise AssertionError("Recovered attempt retained its fence")
        with engine.begin() as db:
            db.execute(
                text(
                    "UPDATE ingestion_outbox SET available_at = clock_timestamp() - interval '1 second' "
                    "WHERE generation = 3"
                )
            )
        active = store.claim(job_id, 3)
        assert active.number == 3
        with Session(engine) as db:
            assert len(db.exec(select(IngestionAttempt)).all()) == 3
            # The same model->job lock order as owner cancellation.
            model = db.exec(select(IfcModel).where(IfcModel.id == active.job.model_id).with_for_update()).one()
            job = db.exec(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()).one()
            job.status, job.cancellation_requested = "cancelled", True
            job.fencing_token += 1
            db.add(job)
            db.commit()
        try:
            store.publish(active, lambda *args: "forbidden")
        except LeaseLost:
            pass
        else:
            raise AssertionError("Cancelled attempt published")
        with engine.begin() as db:
            db.execute(text("UPDATE ingestion_outbox SET delivered_at = clock_timestamp() - interval '121 seconds'"))
        assert dispatch_pending(ahead, broker) == 0
        from scripts.check_publication_concurrency import check_publication_registration

        check_publication_registration(engine, active.job.upload_session_id)
        print(
            "PostgreSQL: concurrent reconciliation, pre-claim failures, skewed clocks, retry availability, "
            "publication expiry/cancellation fence, real graph commit crashes and concurrent deletion passed in "
            f"{time.monotonic() - started:.2f}s"
        )
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    check(parser.parse_args().database_url)


if __name__ == "__main__":
    main()
