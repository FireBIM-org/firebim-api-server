from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.models.models import User
from app.schemas.lexicon import (
    LexiconMapping,
    LexiconMappingCreateRequest,
    LexiconMappingSummary,
)


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
        self.bucket_name = bucket_name or settings.S3_BUCKET_NAME
        self.client = s3_client or boto3.client(
            "s3",
            endpoint_url=endpoint_url or settings.S3_ENDPOINT_URL,
            aws_access_key_id=access_key_id or settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=(secret_access_key or settings.S3_SECRET_ACCESS_KEY.get_secret_value()),
            region_name=region_name or settings.S3_REGION,
        )

    @staticmethod
    def _user_prefix(user: User | SimpleNamespace) -> str:
        return quote(str(user.id).strip(), safe="")

    def _mapping_prefix(self, user: User | SimpleNamespace) -> str:
        return f"{self.ROOT_PREFIX}/{self._user_prefix(user)}/"

    def _mapping_key(self, user: User | SimpleNamespace, mapping_id: str) -> str:
        mapping_id = str(mapping_id).strip()
        if not mapping_id:
            raise ValueError("mapping_id must not be empty")
        return f"{self._mapping_prefix(user)}{quote(mapping_id, safe='')}.json"

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
        key = self._mapping_key(user, mapping_id)
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise LexiconMappingNotFoundError(f"Lexicon mapping not found: {mapping_id}") from exc
            raise LexiconMappingRepositoryError(f"Lexicon mapping fetch failed: {exc}") from exc
        except BotoCoreError as exc:
            raise LexiconMappingRepositoryError(f"Lexicon mapping fetch failed: {exc}") from exc

        try:
            payload = response["Body"].read().decode("utf-8")
            return LexiconMapping.model_validate_json(payload)
        except Exception as exc:
            raise LexiconMappingRepositoryError(f"Lexicon mapping payload is invalid: {exc}") from exc

    def _write_mapping(self, user: User | SimpleNamespace, mapping: LexiconMapping) -> LexiconMapping:
        key = self._mapping_key(user, mapping.id)
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=mapping.model_dump_json(indent=2).encode("utf-8"),
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as exc:
            raise LexiconMappingRepositoryError(f"Lexicon mapping write failed: {exc}") from exc
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
        items: list[LexiconMappingSummary] = []
        continuation_token: str | None = None
        prefix = self._mapping_prefix(user)

        while True:
            kwargs = {
                "Bucket": self.bucket_name,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            try:
                response = self.client.list_objects_v2(**kwargs)
            except (BotoCoreError, ClientError) as exc:
                raise LexiconMappingRepositoryError(f"Lexicon mapping list failed: {exc}") from exc

            for obj in response.get("Contents", []):
                mapping_id = obj["Key"].rsplit("/", 1)[-1].removesuffix(".json")
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

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def delete_mapping(self, mapping_id: str, user: User | SimpleNamespace) -> None:
        key = self._mapping_key(user, mapping_id)
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise LexiconMappingNotFoundError(f"Lexicon mapping not found: {mapping_id}") from exc
            raise LexiconMappingRepositoryError(f"Lexicon mapping delete failed: {exc}") from exc
        except BotoCoreError as exc:
            raise LexiconMappingRepositoryError(f"Lexicon mapping delete failed: {exc}") from exc
