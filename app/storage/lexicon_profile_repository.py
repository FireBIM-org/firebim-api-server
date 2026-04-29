from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from time import monotonic
from types import SimpleNamespace

from app.core.observability import is_request_profiling_enabled, log_event, record_metric
from app.models.models import User
from app.schemas.lexicon import (
    LexiconProfileCreateRequest,
    LexiconProfileSummaryItem,
    LexiconStoredProfile,
)
from app.storage.s3_json_store import S3JsonStoreError, S3JsonStoreNotFoundError, UserScopedS3JsonStore

logger = logging.getLogger(__name__)


class LexiconProfileRepositoryError(Exception):
    pass


class LexiconProfileNotFoundError(LexiconProfileRepositoryError):
    pass


class LexiconProfileRepository:
    ROOT_PREFIX = "lexicon/profiles"

    def __init__(
        self,
        *,
        bucket_name: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region_name: str | None = None,
        s3_client=None,
    ) -> None:
        self.store = UserScopedS3JsonStore(
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region_name=region_name,
            s3_client=s3_client,
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _default_name(payload: LexiconProfileCreateRequest) -> str:
        if payload.name and payload.name.strip():
            return payload.name.strip()
        if payload.rule_id:
            return payload.rule_id
        if payload.rule_ids:
            return f"{len(payload.rule_ids)}-rule profile"
        if payload.rule:
            return payload.rule.id
        if payload.rules:
            return f"{len(payload.rules)}-rule profile"
        return "Lexicon profile"

    def _read_profile(self, user: User | SimpleNamespace, profile_id: str) -> LexiconStoredProfile:
        started_at = monotonic()
        try:
            profile = self.store.read_model(
                self.ROOT_PREFIX,
                user,
                profile_id,
                model_type=LexiconStoredProfile,
                not_found_message=f"Lexicon profile not found: {profile_id}",
                fetch_error_message="Lexicon profile fetch failed",
                payload_error_message="Lexicon profile payload is invalid",
            )
        except S3JsonStoreNotFoundError as exc:
            raise LexiconProfileNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise LexiconProfileRepositoryError(str(exc)) from exc
        duration_ms = round((monotonic() - started_at) * 1000, 3)
        record_metric("lexicon.profile_repo.read_ms", duration_ms, append=True)
        if is_request_profiling_enabled():
            log_event(logger, "lexicon.profile_repo.read", profile_id=profile_id, duration_ms=duration_ms)
        return profile

    def _write_profile(self, user: User | SimpleNamespace, profile: LexiconStoredProfile) -> LexiconStoredProfile:
        started_at = monotonic()
        try:
            self.store.write_model(
                self.ROOT_PREFIX,
                user,
                profile.id,
                profile,
                write_error_message="Lexicon profile write failed",
            )
        except S3JsonStoreError as exc:
            raise LexiconProfileRepositoryError(str(exc)) from exc
        duration_ms = round((monotonic() - started_at) * 1000, 3)
        record_metric("lexicon.profile_repo.write_ms", duration_ms, append=True)
        if is_request_profiling_enabled():
            log_event(logger, "lexicon.profile_repo.write", profile_id=profile.id, duration_ms=duration_ms)
        return profile

    def create_profile(
        self,
        *,
        payload: LexiconProfileCreateRequest,
        user: User | SimpleNamespace,
        canonical_family_uri: str,
        canonical_country_uri: str,
        rule_ids: list[str],
        profile,
        rule_profiles,
        selected_ontologies,
        canonical_target_ontology,
    ) -> LexiconStoredProfile:
        now = self._now()
        record = LexiconStoredProfile(
            id=str(uuid.uuid4()),
            name=self._default_name(payload),
            description=payload.description,
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            canonical_family_uri=canonical_family_uri,
            canonical_country_uri=canonical_country_uri,
            selected_ontologies=selected_ontologies,
            canonical_target_ontology=canonical_target_ontology,
            rule_ids=rule_ids,
            keywords=payload.keywords,
            profile=profile,
            rule_profiles=rule_profiles,
            created_at=now,
            updated_at=now,
        )
        return self._write_profile(user, record)

    def update_profile(
        self,
        *,
        profile_id: str,
        user: User | SimpleNamespace,
        updated: LexiconStoredProfile,
    ) -> LexiconStoredProfile:
        record = updated.model_copy(update={"id": profile_id, "updated_at": self._now()})
        return self._write_profile(user, record)

    def get_profile(self, profile_id: str, user: User | SimpleNamespace) -> LexiconStoredProfile:
        return self._read_profile(user, profile_id)

    def list_profiles(self, user: User | SimpleNamespace) -> list[LexiconProfileSummaryItem]:
        started_at = monotonic()
        try:
            profile_ids = self.store.list_document_ids(self.ROOT_PREFIX, user)
        except S3JsonStoreError as exc:
            raise LexiconProfileRepositoryError(f"Lexicon profile list failed: {exc}") from exc

        items: list[LexiconProfileSummaryItem] = []
        for profile_id in profile_ids:
            record = self._read_profile(user, profile_id)
            property_requirement_count = sum(len(target.property_requirements) for target in record.profile.targets)
            items.append(
                LexiconProfileSummaryItem(
                    id=record.id,
                    name=record.name,
                    description=record.description,
                    ontology_family=record.ontology_family,
                    country=record.country,
                    version=record.version,
                    rule_ids=record.rule_ids,
                    selected_ontologies=record.selected_ontologies,
                    keywords=record.keywords,
                    target_count=len(record.profile.targets),
                    property_requirement_count=property_requirement_count,
                    updated_at=record.updated_at,
                )
            )
        sorted_items = sorted(items, key=lambda item: item.updated_at, reverse=True)
        duration_ms = round((monotonic() - started_at) * 1000, 3)
        record_metric("lexicon.profile_repo.list_ms", duration_ms)
        if is_request_profiling_enabled():
            log_event(logger, "lexicon.profile_repo.list", count=len(sorted_items), duration_ms=duration_ms)
        return sorted_items

    def delete_profile(self, profile_id: str, user: User | SimpleNamespace) -> None:
        started_at = monotonic()
        try:
            self.store.delete_document(
                self.ROOT_PREFIX,
                user,
                profile_id,
                not_found_message=f"Lexicon profile not found: {profile_id}",
                delete_error_message="Lexicon profile delete failed",
            )
        except S3JsonStoreNotFoundError as exc:
            raise LexiconProfileNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise LexiconProfileRepositoryError(str(exc)) from exc
        duration_ms = round((monotonic() - started_at) * 1000, 3)
        record_metric("lexicon.profile_repo.delete_ms", duration_ms, append=True)
        if is_request_profiling_enabled():
            log_event(logger, "lexicon.profile_repo.delete", profile_id=profile_id, duration_ms=duration_ms)
