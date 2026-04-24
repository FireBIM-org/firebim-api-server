from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.models.models import User
from app.schemas.ontologies import (
    OntologyDictionaryCandidate,
    OntologyDictionaryCandidateCreateRequest,
    OntologyDictionaryCandidateSummary,
    OntologyDictionaryValidationReport,
)


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

    def _candidate_prefix(self, user: User | SimpleNamespace) -> str:
        return f"{self.ROOT_PREFIX}/{self._user_prefix(user)}/"

    def _candidate_key(self, user: User | SimpleNamespace, candidate_id: str) -> str:
        candidate_id = str(candidate_id).strip()
        if not candidate_id:
            raise ValueError("candidate_id must not be empty")
        return f"{self._candidate_prefix(user)}{quote(candidate_id, safe='')}.json"

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _default_name(payload: OntologyDictionaryCandidateCreateRequest) -> str:
        if payload.name and payload.name.strip():
            return payload.name.strip()
        return f"{payload.ontology_family}/{payload.country}/{payload.version} candidate"

    def _read_candidate(self, user: User | SimpleNamespace, candidate_id: str) -> OntologyDictionaryCandidate:
        key = self._candidate_key(user, candidate_id)
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise OntologyCandidateNotFoundError(
                    f"Ontology dictionary candidate not found: {candidate_id}"
                ) from exc
            raise OntologyCandidateRepositoryError(f"Ontology dictionary candidate fetch failed: {exc}") from exc
        except BotoCoreError as exc:
            raise OntologyCandidateRepositoryError(f"Ontology dictionary candidate fetch failed: {exc}") from exc

        try:
            payload = response["Body"].read().decode("utf-8")
            return OntologyDictionaryCandidate.model_validate_json(payload)
        except Exception as exc:
            raise OntologyCandidateRepositoryError(f"Ontology dictionary candidate payload is invalid: {exc}") from exc

    def _write_candidate(
        self,
        user: User | SimpleNamespace,
        candidate: OntologyDictionaryCandidate,
    ) -> OntologyDictionaryCandidate:
        key = self._candidate_key(user, candidate.id)
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=candidate.model_dump_json(indent=2).encode("utf-8"),
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as exc:
            raise OntologyCandidateRepositoryError(f"Ontology dictionary candidate write failed: {exc}") from exc
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
        items: list[OntologyDictionaryCandidateSummary] = []
        continuation_token: str | None = None
        prefix = self._candidate_prefix(user)

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
                raise OntologyCandidateRepositoryError(f"Ontology dictionary candidate list failed: {exc}") from exc

            for obj in response.get("Contents", []):
                candidate_id = obj["Key"].rsplit("/", 1)[-1].removesuffix(".json")
                candidate = self._read_candidate(user, candidate_id)
                items.append(self._summary(candidate))

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

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
        key = self._candidate_key(user, candidate_id)
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise OntologyCandidateNotFoundError(
                    f"Ontology dictionary candidate not found: {candidate_id}"
                ) from exc
            raise OntologyCandidateRepositoryError(f"Ontology dictionary candidate delete failed: {exc}") from exc
        except BotoCoreError as exc:
            raise OntologyCandidateRepositoryError(f"Ontology dictionary candidate delete failed: {exc}") from exc

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
