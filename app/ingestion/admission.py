"""Revision-bound admission. No broker or processing task is called in this module."""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.ingestion.clock import database_now
from app.ingestion.jobs import AdmissionError, AdmissionRuntime, content_digest, require_supported_revision
from app.ingestion.neo_ifcowl_adapter import NeoAdapterError
from app.models.models import IfcModel, IfcUploadSession, IngestionAttempt, IngestionJob, IngestionOutbox, User
from app.schemas.models import (
    IfcUploadInitiate,
    IfcUploadResponse,
    IngestionReportResponse,
    IngestionReprocessRequest,
    IngestionRetryRequest,
    IngestionRevisionResponse,
    IngestionRevisionsResponse,
)
from app.storage.ingestion_objects import IngestionObjects
from app.storage.translation_profile_repository import (
    TranslationProfileNotFoundError,
    TranslationProfileRepository,
    TranslationProfileRepositoryError,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class IngestionAdmission:
    def __init__(
        self, db: Session, profiles: TranslationProfileRepository, objects: IngestionObjects, runtime: AdmissionRuntime
    ):
        self.db = db
        self.profiles = profiles
        self.objects = objects
        self.runtime = runtime

    def _revision(self, profile_id, revision_id, user):
        try:
            revision = self.profiles.get_revision(profile_id, revision_id, user)
            require_supported_revision(revision)
            return revision
        except TranslationProfileNotFoundError as exc:
            raise AdmissionError(404, "revision_not_found", "Published translation revision not found") from exc
        except TranslationProfileRepositoryError as exc:
            raise AdmissionError(503, "revision_unavailable", "Published revision could not be verified") from exc
        except NeoAdapterError as exc:
            raise AdmissionError(422, exc.code, str(exc)) from exc

    def supported_revisions(self, user: User, offset: int = 0, limit: int = 50) -> IngestionRevisionsResponse:
        self.runtime.require_available()
        try:
            result = []
            for profile in self.profiles.list_profiles(user):
                for reference in self.profiles.list_revisions(profile.id, user):
                    try:
                        revision = self.profiles.get_revision(profile.id, reference.revision_id, user)
                        require_supported_revision(revision)
                    except (TranslationProfileRepositoryError, NeoAdapterError):
                        # A corrupt/unavailable revision is never offered as executable.
                        continue
                    result.append(
                        IngestionRevisionResponse(
                            profile_id=profile.id,
                            profile_name=revision.content.name,
                            revision_id=revision.revision_id,
                            revision_digest=revision.content_digest,
                            published_at=revision.published_at,
                        )
                    )
        except TranslationProfileRepositoryError as exc:
            raise AdmissionError(503, "revision_unavailable", "Published revisions are unavailable") from exc
        return IngestionRevisionsResponse(
            revisions=result[offset : offset + limit],
            count=len(result),
            max_input_bytes=self.runtime.max_input_bytes,
            runtime_fingerprint=self.runtime.fingerprint(),
        )

    def initiate(self, payload: IfcUploadInitiate, user: User) -> IfcUploadResponse:
        digest = content_digest(payload.model_dump(mode="json"))
        existing = self.db.exec(
            select(IfcUploadSession).where(
                IfcUploadSession.user_id == user.id,
                IfcUploadSession.request_id == str(payload.request_id),
            )
        ).first()
        if existing:
            if existing.request_digest != digest:
                raise AdmissionError(409, "request_reused", "Upload request ID was used with different input")
            return self.upload_status(existing.id, user)
        self.runtime.require_available()
        if payload.size_bytes > self.runtime.max_input_bytes:
            raise AdmissionError(413, "input_too_large", "Upload exceeds the configured qualified input limit")
        revision = self._revision(payload.profile_id, payload.revision_id, user)
        upload_id = str(uuid.uuid4())
        upload = IfcUploadSession(
            id=upload_id,
            model_id=str(uuid.uuid4()),
            user_id=user.id,
            request_id=str(payload.request_id),
            request_digest=digest,
            filename=payload.filename,
            staging_key=f"ingestion/staging/{quote(user.id, safe='')}/{upload_id}.ifc",
            expected_size=payload.size_bytes,
            expected_sha256=payload.sha256,
            profile_id=revision.content.profile_id,
            revision_id=revision.revision_id,
            revision_digest=revision.content_digest,
            revision_snapshot=revision.model_dump(mode="json"),
            runtime_fingerprint=self.runtime.fingerprint(),
            runtime_config=self.runtime.configuration(),
            expires_at=datetime.now(UTC) + timedelta(seconds=self.runtime.upload_ttl_seconds),
        )
        self.db.add(upload)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.db.exec(
                select(IfcUploadSession).where(
                    IfcUploadSession.user_id == user.id,
                    IfcUploadSession.request_id == str(payload.request_id),
                )
            ).first()
            if existing is None or existing.request_digest != digest:
                raise AdmissionError(
                    409,
                    "request_reused",
                    "Upload request conflicts with an existing admission",
                ) from None
            upload = existing
        return self.upload_status(upload.id, user)

    def _upload(self, upload_id: str, user: User) -> IfcUploadSession:
        upload = self.db.exec(
            select(IfcUploadSession)
            .where(
                IfcUploadSession.id == upload_id,
                IfcUploadSession.user_id == user.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if upload is None:
            raise AdmissionError(404, "upload_not_found", "Upload session not found")
        return upload

    def upload_status(self, upload_id: str, user: User) -> IfcUploadResponse:
        upload = self._upload(upload_id, user)
        response = IfcUploadResponse(
            id=upload.id,
            model_id=upload.model_id,
            status=upload.status,
            expires_at=upload.expires_at,
            job_id=upload.job_id,
        )
        if upload.job_id:
            self.get_job(upload.job_id, user)
            return response
        if _utc(upload.expires_at) <= datetime.now(UTC):
            raise AdmissionError(410, "upload_expired", "Upload session expired; initiate a new upload")
        self.runtime.require_available()
        response.upload_url = self.objects.upload_url(
            upload.staging_key,
            upload.expected_size,
            max(1, int((_utc(upload.expires_at) - datetime.now(UTC)).total_seconds())),
        )
        response.upload_headers = {"Content-Type": "application/octet-stream"}
        return response

    def complete(self, upload_id: str, user: User) -> IngestionJob:
        upload = self._upload(upload_id, user)
        if upload.job_id:
            return self.get_job(upload.job_id, user)
        self.runtime.require_available()
        if _utc(upload.expires_at) <= datetime.now(UTC):
            raise AdmissionError(410, "upload_expired", "Upload session expired; initiate a new upload")
        if upload.runtime_fingerprint != self.runtime.fingerprint():
            raise AdmissionError(409, "runtime_changed", "Runtime configuration changed; initiate a new upload")
        revision = self._revision(upload.profile_id, upload.revision_id, user)
        if revision.content_digest != upload.revision_digest:
            raise AdmissionError(409, "revision_changed", "Selected revision no longer matches this upload")
        raw_key = f"ingestion/raw/{quote(user.id, safe='')}/{upload.id}/{upload.expected_sha256}.ifc"
        self.objects.finalize(upload.staging_key, raw_key, upload.expected_size, upload.expected_sha256)
        model = IfcModel(
            id=upload.model_id,
            filename=upload.filename,
            s3_key=raw_key,
            user_id=user.id,
            ingestion_backend="neo",
            processing_status="queued",
            conversion_profile_id=upload.profile_id,
            conversion_profile_name=revision.content.name,
        )
        job = IngestionJob(
            user_id=user.id,
            model_id=model.id,
            upload_session_id=upload.id,
            request_id="upload:" + upload.id,
            request_digest=upload.request_digest,
            raw_key=raw_key,
            raw_sha256=upload.expected_sha256,
            raw_size=upload.expected_size,
            profile_id=upload.profile_id,
            revision_id=upload.revision_id,
            revision_digest=upload.revision_digest,
            revision_snapshot=upload.revision_snapshot,
            runtime_fingerprint=upload.runtime_fingerprint,
            runtime_config=upload.runtime_config,
        )
        model.latest_job_id = job.id
        upload.status = "completed"
        upload.completed_at = datetime.now(UTC)
        upload.job_id = job.id
        upload.raw_key, upload.raw_sha256, upload.raw_size = raw_key, job.raw_sha256, job.raw_size
        upload.source_schema = "IFC4"
        try:
            self.db.add(model)
            self.db.add(upload)
            self.db.flush()
            self.db.add(job)
            self.db.flush()
            self.db.add(IngestionOutbox(job_id=job.id, available_at=database_now(self.db)))
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            winner = self._upload(upload_id, user)
            if winner.job_id is None:
                raise
            return self.get_job(winner.job_id, user)
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(job)
        return job

    def _model(self, model_id: str, user: User) -> IfcModel:
        model = self.db.exec(
            select(IfcModel)
            .where(
                IfcModel.id == model_id,
                IfcModel.user_id == user.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if model is None or model.deleted_at is not None:
            raise AdmissionError(404, "model_not_found", "Model not found")
        if model.ingestion_backend != "neo":
            raise AdmissionError(409, "legacy_model", "This operation is only available for Neo ingestion models")
        return model

    def get_job(self, job_id: str, user: User) -> IngestionJob:
        job = self.db.exec(
            select(IngestionJob).where(
                IngestionJob.id == job_id,
                IngestionJob.user_id == user.id,
            )
        ).first()
        if job is None:
            raise AdmissionError(404, "job_not_found", "Ingestion job not found")
        self._model(job.model_id, user)
        return job

    def list_jobs(self, model_id: str, user: User, offset: int = 0, limit: int = 50) -> list[IngestionJob]:
        self._model(model_id, user)
        return list(
            self.db.exec(
                select(IngestionJob)
                .where(
                    IngestionJob.model_id == model_id,
                    IngestionJob.user_id == user.id,
                )
                .order_by(IngestionJob.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )

    def raw_input(self, model_id: str, user: User) -> bytes:
        self._model(model_id, user)
        upload = self.db.exec(select(IfcUploadSession).where(IfcUploadSession.model_id == model_id)).one()
        return self.objects.read_verified(upload.raw_key, upload.raw_size, upload.raw_sha256)

    def report(self, job_id: str, user: User) -> IngestionReportResponse:
        job = self.get_job(job_id, user)
        attempts = self.db.exec(
            select(IngestionAttempt)
            .where(
                IngestionAttempt.job_id == job_id,
            )
            .order_by(IngestionAttempt.number)
        ).all()
        return IngestionReportResponse(
            job_id=job.id,
            report_version=job.report.get("report_version", "neo-ingestion/1"),
            diagnostics=job.report.get("diagnostics", []),
            attempts=[
                {
                    "number": item.number,
                    "status": item.status,
                    "stage": item.stage,
                    "diagnostics": item.diagnostics,
                    "started_at": item.started_at,
                    "completed_at": item.completed_at,
                }
                for item in attempts
            ],
        )

    def retry(self, job_id: str, payload: IngestionRetryRequest, user: User) -> IngestionJob:
        job = self.get_job(job_id, user)
        # All admission state changes lock the model first, including reprocessing and deletion.
        self.db.refresh(job, with_for_update=True)
        previous = self.db.exec(
            select(IngestionOutbox).where(
                IngestionOutbox.job_id == job.id,
                IngestionOutbox.request_id == str(payload.request_id),
            )
        ).first()
        if previous:
            if previous.generation != payload.expected_dispatch_generation + 1:
                raise AdmissionError(409, "request_reused", "Retry request ID was used with different input")
            return job
        self.runtime.require_available()
        if (
            job.status != "failed"
            or not job.retryable
            or (job.cancellation_requested or job.dispatch_generation != payload.expected_dispatch_generation)
        ):
            raise AdmissionError(409, "retry_not_permitted", "This job cannot be retried in its current state")
        if job.runtime_fingerprint != self.runtime.fingerprint():
            raise AdmissionError(409, "runtime_changed", "Runtime changed; explicitly reprocess instead")
        active = self.db.exec(
            select(IngestionJob.id).where(
                IngestionJob.model_id == job.model_id,
                IngestionJob.id != job.id,
                IngestionJob.status.in_(["queued", "running", "retry_wait"]),
            )
        ).first()
        if active:
            raise AdmissionError(409, "job_active", "The model already has an active ingestion job")
        revision = self._revision(job.profile_id, job.revision_id, user)
        if revision.content_digest != job.revision_digest:
            raise AdmissionError(409, "revision_changed", "Selected revision no longer matches this job")
        self.objects.read_verified(job.raw_key, job.raw_size, job.raw_sha256)
        job.status, job.stage = "queued", "admitted"
        job.max_attempts = max(job.max_attempts, job.attempt_count + 1)
        job.dispatch_generation += 1
        job.retryable = False
        job.completed_at = None
        job.updated_at = datetime.now(UTC)
        model = self._model(job.model_id, user)
        model.latest_job_id, model.processing_status = job.id, "queued"
        self.db.add(job)
        self.db.add(model)
        self.db.add(
            IngestionOutbox(
                job_id=job.id,
                generation=job.dispatch_generation,
                request_id=str(payload.request_id),
                available_at=database_now(self.db),
            )
        )
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            job = self.get_job(job_id, user)
            previous = self.db.exec(
                select(IngestionOutbox).where(
                    IngestionOutbox.job_id == job.id,
                    IngestionOutbox.request_id == str(payload.request_id),
                )
            ).first()
            if previous and previous.generation == payload.expected_dispatch_generation + 1:
                return job
            raise AdmissionError(409, "retry_conflict", "A concurrent admission changed this job") from None
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(job)
        return job

    def reprocess(self, model_id: str, payload: IngestionReprocessRequest, user: User) -> IngestionJob:
        model = self._model(model_id, user)
        digest = content_digest({"model_id": model_id, **payload.model_dump(mode="json")})
        previous = self.db.exec(
            select(IngestionJob).where(
                IngestionJob.user_id == user.id,
                IngestionJob.request_id == str(payload.request_id),
            )
        ).first()
        if previous:
            if previous.request_digest != digest:
                raise AdmissionError(409, "request_reused", "Reprocess request ID was used with different input")
            return previous
        self.runtime.require_available()
        active = self.db.exec(
            select(IngestionJob.id).where(
                IngestionJob.model_id == model_id,
                IngestionJob.status.in_(["queued", "running", "retry_wait"]),
            )
        ).first()
        if active:
            raise AdmissionError(409, "job_active", "The model already has an active ingestion job")
        upload = self.db.exec(select(IfcUploadSession).where(IfcUploadSession.model_id == model_id)).one()
        if upload.raw_size > self.runtime.max_input_bytes:
            raise AdmissionError(413, "input_too_large", "Retained input exceeds the configured input limit")
        revision = self._revision(payload.profile_id, payload.revision_id, user)
        self.objects.read_verified(upload.raw_key, upload.raw_size, upload.raw_sha256)
        job = IngestionJob(
            user_id=user.id,
            model_id=model_id,
            upload_session_id=upload.id,
            request_id=str(payload.request_id),
            request_digest=digest,
            raw_key=upload.raw_key,
            raw_sha256=upload.raw_sha256,
            raw_size=upload.raw_size,
            profile_id=payload.profile_id,
            revision_id=revision.revision_id,
            revision_digest=revision.content_digest,
            revision_snapshot=revision.model_dump(mode="json"),
            runtime_fingerprint=self.runtime.fingerprint(),
            runtime_config=self.runtime.configuration(),
        )
        model.latest_job_id, model.processing_status = job.id, "queued"
        self.db.add(job)
        self.db.add(model)
        try:
            self.db.flush()
            self.db.add(IngestionOutbox(job_id=job.id, available_at=database_now(self.db)))
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            previous = self.db.exec(
                select(IngestionJob).where(
                    IngestionJob.user_id == user.id,
                    IngestionJob.request_id == str(payload.request_id),
                )
            ).first()
            if previous and previous.request_digest == digest:
                return previous
            raise AdmissionError(
                409,
                "reprocess_conflict",
                "A concurrent admission conflicts with this request",
            ) from None
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(job)
        return job

    def cancel(self, job_id: str, user: User) -> IngestionJob:
        job = self.get_job(job_id, user)
        self.db.refresh(job, with_for_update=True)
        if job.status == "succeeded":
            raise AdmissionError(409, "job_completed", "A published job cannot be cancelled")
        if job.status != "cancelled":
            job.cancellation_requested = True
            job.status = "cancelled"
            job.fencing_token += 1
            job.lease_expires_at = None
            job.completed_at = job.updated_at = datetime.now(UTC)
            self._cancel_attempts(job.id, job.completed_at)
            self.db.add(job)
            model = self._model(job.model_id, user)
            if model.latest_job_id == job.id:
                model.processing_status = "cancelled"
                self.db.add(model)
            self.db.commit()
            self.db.refresh(job)
        return job

    def _cancel_attempts(self, job_id, now):
        for attempt in self.db.exec(
            select(IngestionAttempt).where(
                IngestionAttempt.job_id == job_id,
                IngestionAttempt.status == "running",
            )
        ).all():
            attempt.status, attempt.completed_at = "cancelled", now
            self.db.add(attempt)

    def delete_model(self, model_id: str, user: User) -> None:
        model = self._model(model_id, user)
        now = datetime.now(UTC)
        for job in self.db.exec(select(IngestionJob).where(IngestionJob.model_id == model_id)).all():
            if job.status not in {"succeeded", "failed", "cancelled"}:
                job.status, job.cancellation_requested = "cancelled", True
                job.fencing_token += 1
                job.completed_at = job.updated_at = now
                job.lease_expires_at = None
                self._cancel_attempts(job.id, now)
                self.db.add(job)
        # Retain private evidence and successful revisions; retention policy is a rollout gate.
        model.deleted_at, model.processing_status = now, "cancelled"
        self.db.add(model)
        self.db.commit()
