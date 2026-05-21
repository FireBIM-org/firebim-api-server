from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.models import User
from app.schemas.lexicon import (
    LexiconMapping,
    LexiconMappingCreateRequest,
    LexiconMappingSummary,
)
from app.storage.s3_json_store import S3JsonStoreError, S3JsonStoreNotFoundError, UserScopedS3JsonStore


class LexiconMappingRepositoryError(Exception):
    pass


class LexiconMappingNotFoundError(LexiconMappingRepositoryError):
    pass


class LexiconMappingRepository:
    ROOT_PREFIX = "lexicon/mappings"

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
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _default_name(payload: LexiconMappingCreateRequest) -> str:
        if payload.name and payload.name.strip():
            return payload.name.strip()
        if payload.mapping_kind == "semantic_assertion":
            return "Semantic assertion"
        return payload.mapping_kind.replace("_", " ").title()

    @staticmethod
    def _source_term_count(payload: LexiconMappingCreateRequest | LexiconMapping) -> int:
        if payload.source_term is not None:
            return 1

        def _count_clauses(clauses) -> int:
            count = 0
            for clause in clauses:
                if clause.term is not None:
                    count += 1
                elif clause.entity is not None:
                    count += len(clause.entity.all_of or clause.entity.any_of)
                elif clause.all_of:
                    count += _count_clauses(clause.all_of)
                elif clause.any_of:
                    count += _count_clauses(clause.any_of)
            return count

        if payload.source_pattern is None:
            return 0
        if payload.source_pattern.all_of:
            return _count_clauses(payload.source_pattern.all_of)
        return _count_clauses(payload.source_pattern.any_of)

    @staticmethod
    def _target_assertion_count(payload: LexiconMappingCreateRequest | LexiconMapping) -> int:
        if payload.target_term is not None:
            return 1
        return len(payload.target_assertions)

    def _read_mapping(self, user: User | SimpleNamespace, mapping_id: str) -> LexiconMapping:
        try:
            return self.store.read_model(
                self.ROOT_PREFIX,
                user,
                mapping_id,
                model_type=LexiconMapping,
                not_found_message=f"Lexicon mapping not found: {mapping_id}",
                fetch_error_message="Lexicon mapping fetch failed",
                payload_error_message="Lexicon mapping payload is invalid",
            )
        except S3JsonStoreNotFoundError as exc:
            raise LexiconMappingNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise LexiconMappingRepositoryError(str(exc)) from exc

    def _write_mapping(self, user: User | SimpleNamespace, mapping: LexiconMapping) -> LexiconMapping:
        try:
            self.store.write_model(
                self.ROOT_PREFIX,
                user,
                mapping.id,
                mapping,
                write_error_message="Lexicon mapping write failed",
            )
        except S3JsonStoreError as exc:
            raise LexiconMappingRepositoryError(str(exc)) from exc
        return mapping

    def create_mapping(self, payload: LexiconMappingCreateRequest, user: User | SimpleNamespace) -> LexiconMapping:
        now = self._now().isoformat()
        mapping = LexiconMapping(
            id=str(uuid.uuid4()),
            name=self._default_name(payload),
            mapping_kind=payload.mapping_kind,
            source_term=payload.source_term,
            target_term=payload.target_term,
            source_pattern=payload.source_pattern,
            target_assertions=payload.target_assertions,
            relationship=payload.relationship,
            notes=payload.notes,
            created_at=now,
            updated_at=now,
        )
        return self._write_mapping(user, mapping)

    def get_mapping(self, mapping_id: str, user: User | SimpleNamespace) -> LexiconMapping:
        return self._read_mapping(user, mapping_id)

    def list_mappings(self, user: User | SimpleNamespace) -> list[LexiconMappingSummary]:
        try:
            mapping_ids = self.store.list_document_ids(self.ROOT_PREFIX, user)
        except S3JsonStoreError as exc:
            raise LexiconMappingRepositoryError(f"Lexicon mapping list failed: {exc}") from exc

        items: list[LexiconMappingSummary] = []
        for mapping_id in mapping_ids:
            mapping = self._read_mapping(user, mapping_id)
            items.append(
                LexiconMappingSummary(
                    id=mapping.id,
                    name=mapping.name,
                    mapping_kind=mapping.mapping_kind,
                    relationship=mapping.relationship,
                    created_at=mapping.created_at,
                    updated_at=mapping.updated_at,
                    source_term_count=self._source_term_count(mapping),
                    target_assertion_count=self._target_assertion_count(mapping),
                )
            )

        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def delete_mapping(self, mapping_id: str, user: User | SimpleNamespace) -> None:
        try:
            self.store.delete_document(
                self.ROOT_PREFIX,
                user,
                mapping_id,
                not_found_message=f"Lexicon mapping not found: {mapping_id}",
                delete_error_message="Lexicon mapping delete failed",
            )
        except S3JsonStoreNotFoundError as exc:
            raise LexiconMappingNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise LexiconMappingRepositoryError(str(exc)) from exc
