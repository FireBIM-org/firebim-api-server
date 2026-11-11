"""Short database transactions; every mutation locks model before job."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from app.ingestion.clock import database_now
from app.ingestion.errors import ProcessingError as ProcessingError
from app.models.models import IfcModel, IngestionAttempt, IngestionJob, IngestionOutbox

LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


class LeaseLost(Exception):
    pass


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@dataclass(frozen=True)
class Claim:
    job: IngestionJob
    number: int
    fencing_token: int
    execution_fingerprint: str


class ExecutionStore:
    def __init__(self, sessions, *, clock=lambda: datetime.now(UTC)):
        self.sessions = sessions
        self.clock = clock

    def now(self, db):
        return database_now(db, self.clock)

    def _lock(self, db, job_id):
        model_id = db.exec(select(IngestionJob.model_id).where(IngestionJob.id == job_id)).first()
        model = db.exec(
            select(IfcModel).where(IfcModel.id == model_id).with_for_update().execution_options(populate_existing=True)
        ).first()
        job = db.exec(
            select(IngestionJob)
            .where(IngestionJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        return model, job

    def _active(self, db, claim):
        model, job = self._lock(db, claim.job.id)
        now = self.now(db)
        if (
            model is None
            or model.deleted_at is not None
            or job is None
            or job.cancellation_requested
            or job.status != "running"
            or job.fencing_token != claim.fencing_token
            or job.dispatch_generation != claim.job.dispatch_generation
            or job.lease_expires_at is None
            or utc(job.lease_expires_at) <= now
        ):
            raise LeaseLost()
        attempt = db.exec(
            select(IngestionAttempt).where(
                IngestionAttempt.job_id == job.id,
                IngestionAttempt.number == claim.number,
            )
        ).one()
        return model, job, attempt

    def claim(self, job_id, generation, execution_fingerprint=None):
        with self.sessions() as db:
            model, job = self._lock(db, job_id)
            now = self.now(db)
            if (
                not model
                or model.deleted_at is not None
                or not job
                or job.cancellation_requested
                or job.dispatch_generation != generation
                or job.status not in {"queued", "retry_wait", "running"}
            ):
                return None
            event = db.exec(
                select(IngestionOutbox).where(
                    IngestionOutbox.job_id == job.id,
                    IngestionOutbox.generation == generation,
                )
            ).first()
            if event is None or utc(event.available_at) > now:
                return None
            if job.status == "running":
                if job.lease_expires_at and utc(job.lease_expires_at) > now:
                    return None
                previous = db.exec(
                    select(IngestionAttempt).where(
                        IngestionAttempt.job_id == job.id,
                        IngestionAttempt.number == job.attempt_count,
                    )
                ).one()
                previous.status, previous.completed_at = "abandoned", now
                previous.diagnostics = [
                    {
                        "code": "lease_expired",
                        "severity": "error",
                        "message": "Attempt lease expired",
                    }
                ]
                db.add(previous)
            if job.attempt_count >= job.max_attempts:
                job.retryable = job.status == "running"
                job.status, job.completed_at, job.updated_at = "failed", now, now
                job.fencing_token += 1
                job.lease_expires_at = None
                job.report = {
                    **job.report,
                    "diagnostics": [
                        {"code": "attempts_exhausted", "severity": "error", "message": "Attempt budget exhausted"}
                    ],
                }
                model.processing_status = "failed"
                db.add(job)
                db.add(model)
                db.commit()
                return None
            job.attempt_count += 1
            job.fencing_token += 1
            job.status, job.stage, job.retryable = "running", "claimed", False
            job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            job.updated_at, job.completed_at = now, None
            fingerprint = execution_fingerprint or job.runtime_fingerprint
            attempt = IngestionAttempt(
                job_id=job.id,
                number=job.attempt_count,
                fencing_token=job.fencing_token,
                runtime_fingerprint=fingerprint,
                started_at=now,
            )
            model.processing_status = "running"
            db.add_all([model, job, attempt])
            db.commit()
            db.refresh(job)
            db.expunge(job)
            return Claim(job, job.attempt_count, job.fencing_token, fingerprint)

    def heartbeat(self, claim, stage=None):
        with self.sessions() as db:
            _, job, attempt = self._active(db, claim)
            now = self.now(db)
            job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            job.updated_at = now
            if stage:
                job.stage = attempt.stage = stage
            db.add_all([job, attempt])
            db.commit()

    def checkpoint(self, claim, stage, checkpoint, *, report=None, advance=True):
        with self.sessions() as db:
            _, job, attempt = self._active(db, claim)
            job.checkpoints = {**job.checkpoints, stage: checkpoint}
            if advance:
                job.stage = attempt.stage = stage
            if report is not None:
                job.report = report
            attempt.artifacts = {**attempt.artifacts, **checkpoint.get("artifacts", {})}
            job.updated_at = self.now(db)
            db.add_all([job, attempt])
            db.commit()

    def fail(self, claim, error, *, report=None):
        with self.sessions() as db:
            model, job, attempt = self._active(db, claim)
            self._fail_locked(db, model, job, attempt, error, report)
            db.commit()

    def _fail_locked(self, db, model, job, attempt, error, report=None):
        now = self.now(db)
        diagnostic = {"code": error.code, "message": str(error), "severity": "error", "stage": job.stage}
        attempt.status, attempt.completed_at = "failed", now
        attempt.diagnostics = [diagnostic]
        job.report = {
            "report_version": "neo-ingestion/2",
            **(report or {}),
            "diagnostics": [*(report or {}).get("summary", {}).get("diagnostics", []), diagnostic],
        }
        job.updated_at, job.lease_expires_at = now, None
        job.retryable = error.transient
        job.fencing_token += 1
        if job.retryable and job.attempt_count < min(job.max_attempts, MAX_ATTEMPTS):
            job.status = "retry_wait"
            job.dispatch_generation += 1
            db.add(
                IngestionOutbox(
                    job_id=job.id,
                    generation=job.dispatch_generation,
                    request_id=f"automatic-{job.dispatch_generation}",
                    available_at=now + timedelta(seconds=5 * 2 ** (job.attempt_count - 1)),
                )
            )
        else:
            job.status, job.completed_at = "failed", now
        model.processing_status = job.status
        db.add_all([model, job, attempt])

    def publish(self, claim, register):
        """Step 3 calls this with a DB-only graph registration callback.

        Private object writes/validation happen BEFORE entering this boundary. The
        callback must not commit: graph visibility, model pointers and job success
        commit together while cancellation/deletion are excluded by the model lock.
        """
        with self.sessions() as db:
            model, job, attempt = self._active(db, claim)
            graph_id = register(db, model, job)
            if not isinstance(graph_id, str) or not graph_id:
                raise ProcessingError("invalid_publication", "Publication must return a graph ID")
            now = self.now(db)
            if utc(job.lease_expires_at) <= now:
                raise LeaseLost()
            job.result_graph_id, job.status, job.stage = graph_id, "succeeded", "published"
            job.updated_at = job.completed_at = now
            job.lease_expires_at, job.retryable = None, False
            attempt.status, attempt.stage, attempt.completed_at = "succeeded", "published", now
            model.latest_successful_graph_id = graph_id
            model.latest_successful_job_id, model.latest_successful_revision_id = job.id, job.revision_id
            model.processing_status, model.processed = "succeeded", True
            db.add_all([model, job, attempt])
            db.commit()
            return graph_id

    def published(self, claim, graph_id):
        with self.sessions() as db:
            job = db.get(IngestionJob, claim.job.id)
            return bool(
                job
                and job.status == "succeeded"
                and job.fencing_token == claim.fencing_token
                and job.result_graph_id == graph_id
                and graph_id
            )

    def attempt_inactive(self, job_id, fence):
        with self.sessions() as db:
            job = db.get(IngestionJob, job_id)
            return job is None or job.fencing_token != fence or job.status != "running"

    def recover_expired(self, limit=50):
        """Dispatch-loop recovery also covers worker death with no broker redelivery."""
        with self.sessions() as db:
            ids = db.exec(
                select(IngestionJob.id)
                .where(
                    IngestionJob.status == "running",
                    IngestionJob.lease_expires_at <= self.now(db),
                )
                .limit(limit)
            ).all()
        for job_id in ids:
            with self.sessions() as db:
                model, job = self._lock(db, job_id)
                now = self.now(db)
                if (
                    model is None
                    or model.deleted_at is not None
                    or job.status != "running"
                    or job.cancellation_requested
                    or job.lease_expires_at is None
                    or utc(job.lease_expires_at) > now
                ):
                    continue
                attempt = db.exec(
                    select(IngestionAttempt).where(
                        IngestionAttempt.job_id == job.id,
                        IngestionAttempt.number == job.attempt_count,
                    )
                ).one()
                self._fail_locked(
                    db, model, job, attempt, ProcessingError("lease_expired", "Worker lease expired", transient=True)
                )
                db.commit()
