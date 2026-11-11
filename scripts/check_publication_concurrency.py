"""Database-only publication boundary checks, called in the disposable PostgreSQL probe."""

import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

from sqlalchemy import event
from sqlmodel import Session, select

from app.ingestion.execution import ExecutionStore, LeaseLost
from app.ingestion.publication import committed_publication, graph_id_for_job
from app.models.models import IfcModel, IngestionJob, IngestionOutbox, PublishedGraph, User
from app.storage.building_repository import default_building_graph_uri


def check_publication_registration(engine, upload_id):
    from app.ingestion.admission import IngestionAdmission
    from app.ingestion.jobs import AdmissionError, AdmissionRuntime

    store = ExecutionStore(lambda: Session(engine))
    user = User(id="probe", authentik_sub="probe", username="probe", email="probe@example.org")

    def seed():
        with Session(engine) as db:
            model = IfcModel(filename="publication.ifc", s3_key="private/raw", user_id=user.id, ingestion_backend="neo")
            db.add(model)
            db.flush()
            job = IngestionJob(
                user_id=user.id,
                model_id=model.id,
                upload_session_id=upload_id,
                request_id=str(uuid.uuid4()),
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
        return store.claim(job_id, 1)

    def register(db, model, job):
        graph_id = graph_id_for_job(job.id)
        # Hash references are fixture data: this probe tests the real DB boundary,
        # not object storage or serialization (covered by pipeline tests).
        reference = {"key": "private/probe", "bytes": 1, "sha256": "hash"}
        db.add(
            PublishedGraph(
                id=graph_id,
                graph_uri=default_building_graph_uri(graph_id),
                user_id=user.id,
                model_id=model.id,
                job_id=job.id,
                profile_id=job.profile_id,
                revision_id=job.revision_id,
                revision_digest=job.revision_digest,
                input_sha256=job.raw_sha256,
                runtime_fingerprint=job.runtime_fingerprint,
                execution_fingerprint="runtime",
                fencing_token=job.fencing_token,
                attempt_number=job.attempt_count,
                triple_count=1,
                destination=reference,
                provenance=reference,
                ontology_artifacts=[],
                manifest=reference,
                report=reference,
            )
        )
        db.flush()
        return graph_id

    for moment in ("before_commit", "after_commit"):
        claim = seed()
        fired = []

        def mark(db, flush_context):
            if any(isinstance(item, PublishedGraph) for item in db.new):
                db.info["publication_probe"] = True

        def crash(db, fired=fired):
            if not fired and db.info.get("publication_probe"):
                fired.append(True)
                raise RuntimeError("Injected commit crash")

        event.listen(Session, "after_flush", mark)
        event.listen(Session, moment, crash)
        try:
            try:
                store.publish(claim, register)
            except RuntimeError:
                assert fired
            else:
                raise AssertionError("Missing commit failure injection")
        finally:
            event.remove(Session, moment, crash)
            event.remove(Session, "after_flush", mark)
        with Session(engine) as db:
            graph = db.get(PublishedGraph, graph_id_for_job(claim.job.id))
            model = db.get(IfcModel, claim.job.model_id)
            job = db.get(IngestionJob, claim.job.id)
            if moment == "before_commit":
                assert graph is None and model.latest_successful_graph_id is None and job.status == "running"
            else:
                assert graph.id == model.latest_successful_graph_id == job.result_graph_id
                assert committed_publication(store, claim) == graph.id

    for operation in ("cancel", "delete"):
        claim = seed()
        inside, release, owner_started = Event(), Event(), Event()

        def held_registration(db, model, job, inside=inside, release=release):
            graph_id = register(db, model, job)
            inside.set()
            assert release.wait(timeout=3)
            return graph_id

        def owner_action(claim=claim, operation=operation, owner_started=owner_started):
            with Session(engine) as db:
                service = IngestionAdmission(db, None, None, AdmissionRuntime())
                owner_started.set()
                if operation == "delete":
                    service.delete_model(claim.job.model_id, user)
                    return "deleted"
                try:
                    service.cancel(claim.job.id, user)
                except AdmissionError as exc:
                    assert exc.status_code == 409
                    return "already_succeeded"
                raise AssertionError("A completed publication was cancelled")

        with ThreadPoolExecutor(max_workers=2) as pool:
            published = pool.submit(store.publish, claim, held_registration)
            assert inside.wait(timeout=2)
            owner = pool.submit(owner_action)
            assert owner_started.wait(timeout=2)
            try:
                owner.result(timeout=0.1)
            except TimeoutError:
                pass
            else:
                raise AssertionError("Owner operation bypassed model/job publication locks")
            finally:
                release.set()
            assert published.result(timeout=3) == graph_id_for_job(claim.job.id)
            assert owner.result(timeout=3) == ("deleted" if operation == "delete" else "already_succeeded")
        if operation == "delete":
            try:
                committed_publication(store, claim)
            except LeaseLost:
                pass
            else:
                raise AssertionError("A deleted model remained publishable")
            with Session(engine) as db:
                assert db.get(IfcModel, claim.job.model_id).deleted_at is not None
                assert len(db.exec(select(PublishedGraph).where(PublishedGraph.job_id == claim.job.id)).all()) == 1
