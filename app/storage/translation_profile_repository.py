from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.models import User
from app.schemas.translation import (
    TranslationProfileCreateRequest,
    TranslationProfileSummary,
    TranslationProfileUpdateRequest,
    TranslationRevisionReference,
    TranslationStoredProfile,
)
from app.schemas.translation_revision import TranslationPublishedRevision, TranslationRevisionContent
from app.storage.s3_json_store import (
    S3JsonStoreConflictError,
    S3JsonStoreError,
    S3JsonStoreNotFoundError,
    UserScopedS3JsonStore,
)


class TranslationProfileRepositoryError(Exception):
    pass


class TranslationProfileNotFoundError(TranslationProfileRepositoryError):
    pass


class TranslationProfileConflictError(TranslationProfileRepositoryError):
    pass


class TranslationProfileRepository:
    ROOT_PREFIX = "lexicon/translation-profiles"
    REVISION_PREFIX = "lexicon/translation-revisions"
    ID_PREFIX = "ifct_"

    def __init__(self, **store_options) -> None:
        self.store = UserScopedS3JsonStore(**store_options)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _read_profile(self, profile_id: str, user: User | SimpleNamespace) -> tuple[TranslationStoredProfile, str]:
        try:
            return self.store.read_model_with_etag(
                self.ROOT_PREFIX,
                user,
                profile_id,
                model_type=TranslationStoredProfile,
                not_found_message=f"Translation profile not found: {profile_id}",
                fetch_error_message="Translation profile fetch failed",
                payload_error_message="Translation profile payload is invalid",
            )
        except S3JsonStoreNotFoundError as exc:
            raise TranslationProfileNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise TranslationProfileRepositoryError(str(exc)) from exc

    def _write_profile(
        self, user: User | SimpleNamespace, record: TranslationStoredProfile, *, expected_etag: str | None
    ) -> TranslationStoredProfile:
        try:
            self.store.write_model_conditionally(
                self.ROOT_PREFIX,
                user,
                record.id,
                record,
                expected_etag=expected_etag,
                write_error_message="Translation profile write failed",
            )
        except S3JsonStoreConflictError as exc:
            raise TranslationProfileConflictError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise TranslationProfileRepositoryError(str(exc)) from exc
        return record

    def create_profile(
        self, *, payload: TranslationProfileCreateRequest, user: User | SimpleNamespace
    ) -> TranslationStoredProfile:
        now = self._now()
        record = TranslationStoredProfile(
            id=f"{self.ID_PREFIX}{uuid.uuid4()}",
            name=payload.name,
            description=payload.description,
            contract=payload.contract,
            created_at=now,
            updated_at=now,
        )
        return self._write_profile(user, record, expected_etag=None)

    def get_profile(self, profile_id: str, user: User | SimpleNamespace) -> TranslationStoredProfile:
        return self._read_profile(profile_id, user)[0]

    def _read_revision(
        self,
        profile_id: str,
        reference: TranslationRevisionReference,
        user: User | SimpleNamespace,
    ) -> TranslationPublishedRevision:
        try:
            revision = self.store.read_model(
                self.REVISION_PREFIX,
                user,
                reference.revision_id,
                model_type=TranslationPublishedRevision,
                not_found_message="Retained translation revision is missing",
                fetch_error_message="Translation revision fetch failed",
                payload_error_message="Translation revision payload is invalid",
            )
            if (
                revision.revision_id != reference.revision_id
                or revision.content.profile_id != profile_id
                or revision.content.draft_generation != reference.draft_generation
                or revision.published_at != reference.published_at
                or revision.content_digest != reference.content_digest
                or revision.content.digest() != reference.content_digest
            ):
                raise S3JsonStoreError("Translation revision integrity check failed")
            return revision
        except (S3JsonStoreError, ValueError) as exc:
            raise TranslationProfileRepositoryError(str(exc)) from exc

    def list_revisions(
        self,
        profile_id: str,
        user: User | SimpleNamespace,
    ) -> list[TranslationRevisionReference]:
        record = self.get_profile(profile_id, user)
        return sorted(record.revision_refs, key=lambda item: item.draft_generation, reverse=True)

    def get_revision(
        self,
        profile_id: str,
        revision_id: str,
        user: User | SimpleNamespace,
    ) -> TranslationPublishedRevision:
        for reference in self.list_revisions(profile_id, user):
            if reference.revision_id == revision_id:
                return self._read_revision(profile_id, reference, user)
        raise TranslationProfileNotFoundError("Translation revision not found")

    def revision_for_generation(
        self,
        profile_id: str,
        generation: int,
        user: User | SimpleNamespace,
    ) -> TranslationPublishedRevision | None:
        for reference in self.list_revisions(profile_id, user):
            if reference.draft_generation == generation:
                return self._read_revision(profile_id, reference, user)
        return None

    def commit_revision(
        self,
        content: TranslationRevisionContent,
        user: User | SimpleNamespace,
    ) -> TranslationPublishedRevision:
        """Persistence boundary only; the publication service must validate the saved contract first.

        Stage a write-once snapshot, then expose it through a single profile CAS. Unlinked
        candidates from failed commits are never readable through revision APIs.
        """
        content = content.model_copy(deep=True)
        try:
            digest = content.digest()
        except ValueError as exc:
            raise TranslationProfileRepositoryError("Revision content is not canonical JSON") from exc
        existing, etag = self._read_profile(content.profile_id, user)
        for reference in existing.revision_refs:
            if reference.draft_generation == content.draft_generation:
                if reference.content_digest != digest:
                    raise TranslationProfileConflictError("This generation already has a different published revision")
                return self._read_revision(content.profile_id, reference, user)
        self.require_draft_generation(existing, content.draft_generation)
        if (content.name, content.description, content.source_contract) != (
            existing.name,
            existing.description,
            existing.contract,
        ):
            raise TranslationProfileConflictError("The revision does not match the saved draft")
        revision = TranslationPublishedRevision(
            revision_id=f"ifctr_{digest}",
            content_digest=digest,
            published_at=self._now(),
            content=content,
        )
        try:
            self.store.write_model_conditionally(
                self.REVISION_PREFIX,
                user,
                revision.revision_id,
                revision,
                expected_etag=None,
                write_error_message="Translation revision write failed",
            )
        except S3JsonStoreConflictError:
            # A concurrent/retried staging operation may already have retained these exact bytes.
            try:
                staged = self.store.read_model(
                    self.REVISION_PREFIX,
                    user,
                    revision.revision_id,
                    model_type=TranslationPublishedRevision,
                    not_found_message="Staged translation revision is missing",
                    fetch_error_message="Staged translation revision fetch failed",
                    payload_error_message="Staged translation revision is invalid",
                )
                reference = TranslationRevisionReference(
                    revision_id=revision.revision_id,
                    draft_generation=content.draft_generation,
                    content_digest=digest,
                    published_at=staged.published_at,
                )
                revision = self._read_revision(content.profile_id, reference, user)
            except S3JsonStoreError as exc:
                raise TranslationProfileRepositoryError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise TranslationProfileRepositoryError(str(exc)) from exc
        reference = TranslationRevisionReference(
            revision_id=revision.revision_id,
            draft_generation=content.draft_generation,
            content_digest=digest,
            published_at=revision.published_at,
        )
        updated = existing.model_copy(
            update={
                "revision_refs": [*existing.revision_refs, reference],
                "generation": existing.generation + 1,
                "updated_at": self._now(),
            }
        )
        try:
            self._write_profile(user, updated, expected_etag=etag)
        except TranslationProfileConflictError:
            committed = self.revision_for_generation(content.profile_id, content.draft_generation, user)
            if committed is not None and committed.content_digest == digest:
                return committed
            raise
        return revision

    @staticmethod
    def require_draft_generation(record: TranslationStoredProfile, expected_generation: int) -> None:
        if record.status != "draft":
            raise TranslationProfileConflictError("Archived translation profiles cannot be edited or published")
        if record.generation != expected_generation:
            raise TranslationProfileConflictError("The draft changed; reload before saving")

    def update_profile(
        self, *, profile_id: str, user: User | SimpleNamespace, payload: TranslationProfileUpdateRequest
    ) -> TranslationStoredProfile:
        existing, etag = self._read_profile(profile_id, user)
        self.require_draft_generation(existing, payload.expected_generation)
        changes = payload.model_dump(exclude_unset=True, exclude={"expected_generation"})
        record = TranslationStoredProfile.model_validate(
            {
                **existing.model_dump(),
                **changes,
                "generation": existing.generation + 1,
                "updated_at": self._now(),
            }
        )
        return self._write_profile(user, record, expected_etag=etag)

    def archive_profile(
        self, profile_id: str, user: User | SimpleNamespace, *, expected_generation: int
    ) -> TranslationStoredProfile:
        existing, etag = self._read_profile(profile_id, user)
        self.require_draft_generation(existing, expected_generation)
        record = existing.model_copy(
            update={"status": "archived", "generation": existing.generation + 1, "updated_at": self._now()}
        )
        return self._write_profile(user, record, expected_etag=etag)

    def list_profiles(
        self, user: User | SimpleNamespace, *, include_archived: bool = False
    ) -> list[TranslationProfileSummary]:
        try:
            profile_ids = self.store.list_document_ids(self.ROOT_PREFIX, user)
        except S3JsonStoreError as exc:
            raise TranslationProfileRepositoryError(str(exc)) from exc
        items = []
        for profile_id in profile_ids:
            record = self.get_profile(profile_id, user)
            if record.status == "archived" and not include_archived:
                continue
            items.append(
                TranslationProfileSummary.model_validate(
                    record.model_dump(include=set(TranslationProfileSummary.model_fields))
                )
            )
        return sorted(items, key=lambda item: item.updated_at, reverse=True)
