from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.models import User
from app.schemas.lexicon import (
    LexiconHandshake,
    LexiconHandshakeAcceptedPayload,
    LexiconSelectedOntology,
)
from app.storage.s3_json_store import S3JsonStoreError, S3JsonStoreNotFoundError, UserScopedS3JsonStore


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

    def _read_handshake(self, user: User | SimpleNamespace, handshake_id: str) -> LexiconHandshake:
        try:
            return self.store.read_model(
                self.ROOT_PREFIX,
                user,
                handshake_id,
                model_type=LexiconHandshake,
                not_found_message=f"Lexicon handshake not found: {handshake_id}",
                fetch_error_message="Lexicon handshake fetch failed",
                payload_error_message="Lexicon handshake payload is invalid",
            )
        except S3JsonStoreNotFoundError as exc:
            raise LexiconHandshakeNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise LexiconHandshakeRepositoryError(str(exc)) from exc

    def _write_handshake(self, user: User | SimpleNamespace, handshake: LexiconHandshake) -> LexiconHandshake:
        try:
            self.store.write_model(
                self.ROOT_PREFIX,
                user,
                handshake.id,
                handshake,
                write_error_message="Lexicon handshake write failed",
            )
        except S3JsonStoreError as exc:
            raise LexiconHandshakeRepositoryError(str(exc)) from exc
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
