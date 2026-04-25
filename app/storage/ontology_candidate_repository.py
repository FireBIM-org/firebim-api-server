from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.models import User
from app.schemas.ontologies import (
    OntologyDictionaryCandidate,
    OntologyDictionaryCandidateCreateRequest,
    OntologyDictionaryCandidateSummary,
    OntologyDictionaryValidationReport,
)
from app.storage.s3_json_store import S3JsonStoreError, S3JsonStoreNotFoundError, UserScopedS3JsonStore


class OntologyCandidateRepositoryError(Exception):
    pass


class OntologyCandidateNotFoundError(OntologyCandidateRepositoryError):
    pass


class OntologyCandidateRepository:
    ROOT_PREFIX = "ontologies/candidates/dictionaries"

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
    def _default_name(payload: OntologyDictionaryCandidateCreateRequest) -> str:
        if payload.name and payload.name.strip():
            return payload.name.strip()
        return f"{payload.ontology_family}/{payload.country}/{payload.version} candidate"

    def _read_candidate(self, user: User | SimpleNamespace, candidate_id: str) -> OntologyDictionaryCandidate:
        try:
            return self.store.read_model(
                self.ROOT_PREFIX,
                user,
                candidate_id,
                model_type=OntologyDictionaryCandidate,
                not_found_message=f"Ontology dictionary candidate not found: {candidate_id}",
                fetch_error_message="Ontology dictionary candidate fetch failed",
                payload_error_message="Ontology dictionary candidate payload is invalid",
            )
        except S3JsonStoreNotFoundError as exc:
            raise OntologyCandidateNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise OntologyCandidateRepositoryError(str(exc)) from exc

    def _write_candidate(
        self,
        user: User | SimpleNamespace,
        candidate: OntologyDictionaryCandidate,
    ) -> OntologyDictionaryCandidate:
        try:
            self.store.write_model(
                self.ROOT_PREFIX,
                user,
                candidate.id,
                candidate,
                write_error_message="Ontology dictionary candidate write failed",
            )
        except S3JsonStoreError as exc:
            raise OntologyCandidateRepositoryError(str(exc)) from exc
        return candidate

    def create_candidate(
        self,
        payload: OntologyDictionaryCandidateCreateRequest,
        *,
        dictionary_json: dict,
        user: User | SimpleNamespace,
    ) -> OntologyDictionaryCandidate:
        now = self._now()
        candidate = OntologyDictionaryCandidate(
            id=str(uuid.uuid4()),
            name=self._default_name(payload),
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            source_resource_filename=payload.source_resource_filename,
            source_dictionary_name=dictionary_json.get("DictionaryName"),
            source_dictionary_code=dictionary_json.get("DictionaryCode"),
            source_dictionary_version=dictionary_json.get("DictionaryVersion"),
            dictionary_json=dictionary_json,
            created_at=now,
            updated_at=now,
            last_validation_report=None,
        )
        return self._write_candidate(user, candidate)

    def get_candidate(self, candidate_id: str, user: User | SimpleNamespace) -> OntologyDictionaryCandidate:
        return self._read_candidate(user, candidate_id)

    def list_candidates(self, user: User | SimpleNamespace) -> list[OntologyDictionaryCandidateSummary]:
        try:
            candidate_ids = self.store.list_document_ids(self.ROOT_PREFIX, user)
        except S3JsonStoreError as exc:
            raise OntologyCandidateRepositoryError(f"Ontology dictionary candidate list failed: {exc}") from exc

        items = [self._summary(self._read_candidate(user, candidate_id)) for candidate_id in candidate_ids]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def update_validation_report(
        self,
        candidate_id: str,
        validation_report: OntologyDictionaryValidationReport,
        user: User | SimpleNamespace,
    ) -> OntologyDictionaryCandidate:
        candidate = self._read_candidate(user, candidate_id)
        updated = candidate.model_copy(
            update={
                "last_validation_report": validation_report,
                "updated_at": self._now(),
            }
        )
        return self._write_candidate(user, updated)

    def delete_candidate(self, candidate_id: str, user: User | SimpleNamespace) -> None:
        try:
            self.store.delete_document(
                self.ROOT_PREFIX,
                user,
                candidate_id,
                not_found_message=f"Ontology dictionary candidate not found: {candidate_id}",
                delete_error_message="Ontology dictionary candidate delete failed",
            )
        except S3JsonStoreNotFoundError as exc:
            raise OntologyCandidateNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise OntologyCandidateRepositoryError(str(exc)) from exc

    @staticmethod
    def _summary(candidate: OntologyDictionaryCandidate) -> OntologyDictionaryCandidateSummary:
        return OntologyDictionaryCandidateSummary(
            id=candidate.id,
            name=candidate.name,
            ontology_family=candidate.ontology_family,
            country=candidate.country,
            version=candidate.version,
            source_resource_filename=candidate.source_resource_filename,
            source_dictionary_name=candidate.source_dictionary_name,
            source_dictionary_code=candidate.source_dictionary_code,
            source_dictionary_version=candidate.source_dictionary_version,
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
            last_validation_report=candidate.last_validation_report,
        )
