from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.core.s3 import BUCKET_NAME, s3_client
from app.schemas.ontologies import OntologySyncJobStatus, OntologySyncRequest
from app.storage.graph_store import GraphStoreError

JOB_PREFIX = "ontology-sync/jobs"


class OntologySyncJobNotFoundError(GraphStoreError):
    pass


class OntologySyncJobStore:
    def __init__(self, *, client=None, bucket_name: str | None = None) -> None:
        self.client = client or s3_client
        self.bucket_name = bucket_name or BUCKET_NAME

    @staticmethod
    def _key(job_id: str) -> str:
        try:
            normalized = str(uuid.UUID(job_id))
        except ValueError as exc:
            raise OntologySyncJobNotFoundError("Ontology sync job not found") from exc
        return f"{JOB_PREFIX}/{normalized}.json"

    def create(self, *, owner_id: str, request: OntologySyncRequest) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        record: dict[str, Any] = {
            "job_id": job_id,
            "owner_id": owner_id,
            "status": OntologySyncJobStatus.queued.value,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "request": request.model_dump(mode="json"),
            "result": None,
            "error": None,
        }
        self._save(record)
        return record

    def get(self, job_id: str) -> dict[str, Any]:
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=self._key(job_id))
            return json.loads(response["Body"].read().decode("utf-8"))
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise OntologySyncJobNotFoundError("Ontology sync job not found") from exc
            raise GraphStoreError(f"Ontology sync job fetch failed: {exc}") from exc
        except BotoCoreError as exc:
            raise GraphStoreError(f"Ontology sync job fetch failed: {exc}") from exc

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        record = self.get(job_id)
        record.update(changes)
        self._save(record)
        return record

    def _save(self, record: dict[str, Any]) -> None:
        payload = json.dumps(record, ensure_ascii=True, sort_keys=True).encode("utf-8")
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=self._key(str(record["job_id"])),
                Body=payload,
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as exc:
            raise GraphStoreError(f"Ontology sync job save failed: {exc}") from exc


__all__ = ["OntologySyncJobNotFoundError", "OntologySyncJobStore"]
