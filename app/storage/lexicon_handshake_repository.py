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
    LexiconHandshake,
    LexiconHandshakeAcceptedPayload,
    LexiconSelectedOntology,
)


class LexiconHandshakeRepositoryError(Exception):
    pass


class LexiconHandshakeNotFoundError(LexiconHandshakeRepositoryError):
    pass


class LexiconHandshakeRepository:
    ROOT_PREFIX = "lexicon/handshakes"

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

    def _handshake_prefix(self, user: User | SimpleNamespace) -> str:
        return f"{self.ROOT_PREFIX}/{self._user_prefix(user)}/"

    def _handshake_key(self, user: User | SimpleNamespace, handshake_id: str) -> str:
        handshake_id = str(handshake_id).strip()
        if not handshake_id:
            raise ValueError("handshake_id must not be empty")
        return f"{self._handshake_prefix(user)}{quote(handshake_id, safe='')}.json"

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _read_handshake(self, user: User | SimpleNamespace, handshake_id: str) -> LexiconHandshake:
        key = self._handshake_key(user, handshake_id)
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise LexiconHandshakeNotFoundError(f"Lexicon handshake not found: {handshake_id}") from exc
            raise LexiconHandshakeRepositoryError(f"Lexicon handshake fetch failed: {exc}") from exc
        except BotoCoreError as exc:
            raise LexiconHandshakeRepositoryError(f"Lexicon handshake fetch failed: {exc}") from exc

        try:
            payload = response["Body"].read().decode("utf-8")
            return LexiconHandshake.model_validate_json(payload)
        except Exception as exc:
            raise LexiconHandshakeRepositoryError(f"Lexicon handshake payload is invalid: {exc}") from exc

    def _write_handshake(self, user: User | SimpleNamespace, handshake: LexiconHandshake) -> LexiconHandshake:
        key = self._handshake_key(user, handshake.id)
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=handshake.model_dump_json(indent=2).encode("utf-8"),
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as exc:
            raise LexiconHandshakeRepositoryError(f"Lexicon handshake write failed: {exc}") from exc
        return handshake

    def create_handshake(
        self,
        *,
        user: User | SimpleNamespace,
        ontology_family: str,
        country: str,
        version: str,
        canonical_family_uri: str,
        canonical_country_uri: str,
        selected_ontologies: list[LexiconSelectedOntology],
        canonical_target_ontology: LexiconSelectedOntology | None,
        profile,
        accepted_payload: LexiconHandshakeAcceptedPayload,
    ) -> LexiconHandshake:
        now = self._now()
        handshake = LexiconHandshake(
            id=str(uuid.uuid4()),
            ontology_family=ontology_family,
            country=country,
            version=version,
            canonical_family_uri=canonical_family_uri,
            canonical_country_uri=canonical_country_uri,
            selected_ontologies=selected_ontologies,
            canonical_target_ontology=canonical_target_ontology,
            profile=profile,
            accepted_payload=accepted_payload,
            created_at=now,
            updated_at=now,
        )
        return self._write_handshake(user, handshake)

    def get_handshake(self, handshake_id: str, user: User | SimpleNamespace) -> LexiconHandshake:
        return self._read_handshake(user, handshake_id)

    def update_accepted_payload(
        self,
        handshake_id: str,
        accepted_payload: LexiconHandshakeAcceptedPayload,
        user: User | SimpleNamespace,
    ) -> LexiconHandshake:
        existing = self._read_handshake(user, handshake_id)
        updated = existing.model_copy(
            update={
                "accepted_payload": accepted_payload,
                "updated_at": self._now(),
            }
        )
        return self._write_handshake(user, updated)
