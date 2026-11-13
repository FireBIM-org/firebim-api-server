from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.ontologies.registry_sync import sync_from_registry
from app.ontologies.sync_jobs import OntologySyncJobStore
from app.schemas.ontologies import OntologySyncJobStatus, OntologySyncRequest
from app.storage.ontology_repository import OntologyRepository
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="sync_ontologies_from_registry")
def sync_ontologies_from_registry(job_id: str, request_payload: dict):
    """Download, parse and publish registry ontologies outside the API request."""
    job_store = OntologySyncJobStore()
    job_store.update(
        job_id,
        status=OntologySyncJobStatus.running.value,
        started_at=datetime.now(UTC).isoformat(),
    )
    try:
        request = OntologySyncRequest.model_validate(request_payload)
        result = sync_from_registry(OntologyRepository(), request)
        job_store.update(
            job_id,
            status=OntologySyncJobStatus.completed.value,
            completed_at=datetime.now(UTC).isoformat(),
            result=result.model_dump(mode="json"),
            error=None,
        )
        return {"status": OntologySyncJobStatus.completed.value, "job_id": job_id}
    except Exception as exc:
        logger.exception("Ontology sync job %s failed", job_id)
        job_store.update(
            job_id,
            status=OntologySyncJobStatus.failed.value,
            completed_at=datetime.now(UTC).isoformat(),
            error=str(exc),
        )
        return {
            "status": OntologySyncJobStatus.failed.value,
            "job_id": job_id,
            "error": str(exc),
        }
