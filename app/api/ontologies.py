from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse

from app.auth.jwt import get_current_active_user
from app.core.config import settings
from app.schemas.ontologies import (
    OntologyDeleteResponse,
    OntologyHealthResponse,
    OntologyPublishRequest,
    OntologyPublishResponse,
    OntologySummaryResponse,
    OntologyVersionFetchResponse,
    OntologyVersionListResponse,
)
from app.storage.graph_store import GraphStoreError, GraphStoreNotFoundError
from app.storage.ontology_repository import ONTOLOGIES_DATASET, OntologyRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ontologies", tags=["ontologies"])

repo = OntologyRepository()


@router.get("/health", response_model=OntologyHealthResponse)
def ontologies_health() -> OntologyHealthResponse:
    connected = repo.health()
    return OntologyHealthResponse(
        status="ok" if connected else "degraded",
        storage_connected=connected,
        dataset=ONTOLOGIES_DATASET,
        storage_backend="s3",
        storage_host=settings.S3_ENDPOINT_URL,
        resource_directories=repo.resource_directories(),
    )


@router.post("/publish", response_model=OntologyPublishResponse)
def publish_ontology(
    payload: OntologyPublishRequest,
    _current_user=Depends(get_current_active_user),
) -> OntologyPublishResponse:
    try:
        published = repo.publish_version(payload)
        return OntologyPublishResponse(success=True, **published)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to publish ontology", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.delete("/{ontology_family}/{country}/versions/{version}", response_model=OntologyDeleteResponse)
def delete_ontology_version(
    ontology_family: str,
    country: str,
    version: str,
    _current_user=Depends(get_current_active_user),
) -> OntologyDeleteResponse:
    try:
        graph_uri = repo.delete_version(ontology_family.strip().lower(), country.strip().lower(), version.strip())
        return OntologyDeleteResponse(
            deleted=True,
            ontology_family=ontology_family.strip().lower(),
            country=country.strip().lower(),
            version=version.strip(),
            graph_uri=graph_uri,
        )
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to delete ontology version", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{ontology_family}/{country}", response_model=OntologySummaryResponse)
def get_ontology_summary(ontology_family: str, country: str) -> OntologySummaryResponse:
    ontology_family = ontology_family.strip().lower()
    country = country.strip().lower()
    try:
        return OntologySummaryResponse(**repo.get_summary(ontology_family, country))
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to get ontology summary", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{ontology_family}/{country}/versions", response_model=OntologyVersionListResponse)
def list_ontology_versions(ontology_family: str, country: str) -> OntologyVersionListResponse:
    ontology_family = ontology_family.strip().lower()
    country = country.strip().lower()
    try:
        return repo.list_versions(ontology_family, country)
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to list ontology versions", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{ontology_family}/{country}/versions/latest", response_model=OntologyVersionFetchResponse)
def fetch_latest_ontology_version(ontology_family: str, country: str) -> OntologyVersionFetchResponse:
    return _fetch_ontology_version(ontology_family, country, "latest")


@router.get("/{ontology_family}/{country}/versions/stable", response_model=OntologyVersionFetchResponse)
def fetch_stable_ontology_version(ontology_family: str, country: str) -> OntologyVersionFetchResponse:
    return _fetch_ontology_version(ontology_family, country, "stable")


@router.get("/{ontology_family}/{country}/versions/{version}", response_model=OntologyVersionFetchResponse)
def fetch_ontology_version(ontology_family: str, country: str, version: str) -> OntologyVersionFetchResponse:
    return _fetch_ontology_version(ontology_family, country, version)


@router.get(
    "/{ontology_family}/{country}/versions/{version}/ttl",
    response_class=PlainTextResponse,
    responses={200: {"description": "Raw ontology Turtle"}},
)
def fetch_ontology_ttl(ontology_family: str, country: str, version: str) -> PlainTextResponse:
    response = _fetch_ontology_version(ontology_family, country, version)
    return PlainTextResponse(response.ttl, media_type="text/turtle; charset=utf-8")


def _fetch_ontology_version(ontology_family: str, country: str, version: str) -> OntologyVersionFetchResponse:
    ontology_family = ontology_family.strip().lower()
    country = country.strip().lower()
    try:
        return repo.fetch_version(ontology_family, country, version.strip())
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to fetch ontology version", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
