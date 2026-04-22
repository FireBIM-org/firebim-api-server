from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth.jwt import get_current_active_user
from app.core.config import settings
from app.models.models import User
from app.schemas.ontologies import (
    OntologiesHealthResponse,
    OntologyDeleteResponse,
    OntologyPublishRequest,
    OntologyPublishResponse,
    OntologySummaryResponse,
    OntologyVersionListResponse,
    OntologyVersionResponse,
)
from app.storage.ontology_repository import (
    OntologyRepository,
    OntologyRepositoryError,
    OntologyRepositoryNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ontologies"])

repo = OntologyRepository()


@router.get("/ontologies/health", response_model=OntologiesHealthResponse)
@router.get("/api/ontologies/health", response_model=OntologiesHealthResponse, include_in_schema=False)
def ontologies_health() -> OntologiesHealthResponse:
    connected = repo.health()
    return OntologiesHealthResponse(
        status="ok" if connected else "degraded",
        storage_connected=connected,
        storage_backend="s3",
        storage_host=settings.S3_ENDPOINT_URL,
        root_prefix=repo.ROOT_PREFIX,
    )


@router.get("/ontologies/{ontology_family}/{country}", response_model=OntologySummaryResponse)
@router.get(
    "/api/ontologies/{ontology_family}/{country}",
    response_model=OntologySummaryResponse,
    include_in_schema=False,
)
def ontology_summary(ontology_family: str, country: str) -> OntologySummaryResponse:
    try:
        return repo.get_summary(ontology_family, country)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyRepositoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyRepositoryError as exc:
        logger.exception("Failed to fetch ontology summary", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/ontologies/{ontology_family}/{country}/versions", response_model=OntologyVersionListResponse)
@router.get(
    "/api/ontologies/{ontology_family}/{country}/versions",
    response_model=OntologyVersionListResponse,
    include_in_schema=False,
)
def ontology_versions(ontology_family: str, country: str) -> OntologyVersionListResponse:
    try:
        return repo.list_versions(ontology_family, country)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyRepositoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyRepositoryError as exc:
        logger.exception("Failed to list ontology versions", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/ontologies/{ontology_family}/{country}/versions/latest", response_model=OntologyVersionResponse)
@router.get(
    "/api/ontologies/{ontology_family}/{country}/versions/latest",
    response_model=OntologyVersionResponse,
    include_in_schema=False,
)
def ontology_latest_version(ontology_family: str, country: str) -> OntologyVersionResponse:
    try:
        return repo.get_latest_version(ontology_family, country)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyRepositoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyRepositoryError as exc:
        logger.exception("Failed to fetch latest ontology version", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/ontologies/{ontology_family}/{country}/versions/stable", response_model=OntologyVersionResponse)
@router.get(
    "/api/ontologies/{ontology_family}/{country}/versions/stable",
    response_model=OntologyVersionResponse,
    include_in_schema=False,
)
def ontology_stable_version(ontology_family: str, country: str) -> OntologyVersionResponse:
    try:
        return repo.get_latest_stable_version(ontology_family, country)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyRepositoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyRepositoryError as exc:
        logger.exception("Failed to fetch stable ontology version", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/ontologies/{ontology_family}/{country}/versions/{version}", response_model=OntologyVersionResponse)
@router.get(
    "/api/ontologies/{ontology_family}/{country}/versions/{version}",
    response_model=OntologyVersionResponse,
    include_in_schema=False,
)
def ontology_version(ontology_family: str, country: str, version: str) -> OntologyVersionResponse:
    try:
        return repo.get_version(ontology_family, country, version)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyRepositoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyRepositoryError as exc:
        logger.exception("Failed to fetch ontology version", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/ontologies/{ontology_family}/{country}/versions/{version}/content")
@router.get("/api/ontologies/{ontology_family}/{country}/versions/{version}/content", include_in_schema=False)
def ontology_version_content(ontology_family: str, country: str, version: str) -> Response:
    try:
        content, content_type = repo.get_version_content(ontology_family, country, version)
        return Response(content=content, media_type=content_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyRepositoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyRepositoryError as exc:
        logger.exception("Failed to fetch ontology content", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/ontologies/publish", response_model=OntologyPublishResponse, status_code=status.HTTP_201_CREATED)
@router.post("/api/ontologies/publish", response_model=OntologyPublishResponse, include_in_schema=False)
def publish_ontology_version(
    payload: OntologyPublishRequest,
    current_user: User = Depends(get_current_active_user),
) -> OntologyPublishResponse:
    try:
        _ = current_user
        result = repo.publish_version(payload)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyRepositoryError as exc:
        logger.exception("Failed to publish ontology version", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.delete("/ontologies/{ontology_family}/{country}/versions/{version}", response_model=OntologyDeleteResponse)
@router.delete(
    "/api/ontologies/{ontology_family}/{country}/versions/{version}",
    response_model=OntologyDeleteResponse,
    include_in_schema=False,
)
def delete_ontology_version(
    ontology_family: str,
    country: str,
    version: str,
    current_user: User = Depends(get_current_active_user),
) -> OntologyDeleteResponse:
    try:
        _ = current_user
        return repo.delete_version(ontology_family, country, version)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyRepositoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyRepositoryError as exc:
        logger.exception("Failed to delete ontology version", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
