from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.lexicon.service import LexiconService
from app.schemas.lexicon import (
    LexiconCatalogTermResponse,
    LexiconCatalogTermsResponse,
    LexiconConformityCheckRequest,
    LexiconConformityCheckResponse,
    LexiconHealthResponse,
    LexiconMappingValidationRequest,
    LexiconMappingValidationResponse,
    LexiconProfileDeriveRequest,
    LexiconProfileDeriveResponse,
    LexiconReconstructionRequest,
    LexiconReconstructionResponse,
)
from app.storage.graph_store import GraphStoreError, GraphStoreNotFoundError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lexicon", tags=["lexicon"])

service = LexiconService()


@router.get("/health", response_model=LexiconHealthResponse)
def lexicon_health() -> LexiconHealthResponse:
    connected = service.health()
    return LexiconHealthResponse(
        status="ok" if connected else "degraded",
        ontologies_connected=connected,
    )


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/terms",
    response_model=LexiconCatalogTermsResponse,
)
def list_catalog_terms(
    ontology_family: str,
    country: str,
    version: str,
    kind: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> LexiconCatalogTermsResponse:
    try:
        return service.list_catalog_terms(
            ontology_family=ontology_family,
            country=country,
            version=version,
            kind=kind,
            search=search,
        )
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to list lexicon catalog terms", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to list lexicon catalog terms",
        ) from exc


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/terms/{term_id}",
    response_model=LexiconCatalogTermResponse,
)
def get_catalog_term(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
) -> LexiconCatalogTermResponse:
    try:
        return service.get_catalog_term(
            ontology_family=ontology_family,
            country=country,
            version=version,
            term_id=term_id,
        )
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to fetch lexicon catalog term", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch lexicon catalog term",
        ) from exc


@router.post("/mappings/validate", response_model=LexiconMappingValidationResponse)
def validate_mapping(payload: LexiconMappingValidationRequest) -> LexiconMappingValidationResponse:
    try:
        return service.validate_mapping(payload=payload)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to validate lexicon mapping", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to validate lexicon mapping",
        ) from exc


@router.post("/profiles/derive", response_model=LexiconProfileDeriveResponse)
def derive_execution_profile(payload: LexiconProfileDeriveRequest) -> LexiconProfileDeriveResponse:
    try:
        ontology, profile = service.derive_profile(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            rule_id=payload.rule_id,
            rule=payload.rule,
        )
        return LexiconProfileDeriveResponse(
            ontology_family=ontology.ontology_family,
            country=ontology.country,
            version=ontology.version,
            canonical_family_uri=ontology.canonical_family_uri,
            canonical_country_uri=ontology.canonical_country_uri,
            profile=profile,
        )
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to derive lexicon execution profile", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to derive lexicon execution profile",
        ) from exc


@router.post("/reconstruction", response_model=LexiconReconstructionResponse)
def reconstruct_canonical_graph(payload: LexiconReconstructionRequest) -> LexiconReconstructionResponse:
    try:
        return service.reconstruct_graph(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            graph_id=payload.graph_id,
            instance_base_uri=payload.instance_base_uri,
            entities=payload.entities,
        )
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to reconstruct canonical lexicon graph", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to reconstruct canonical lexicon graph",
        ) from exc


@router.post("/conformity/check", response_model=LexiconConformityCheckResponse)
def check_lexicon_conformity(payload: LexiconConformityCheckRequest) -> LexiconConformityCheckResponse:
    try:
        return service.check_conformity(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            ttl=payload.ttl,
        )
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to check lexicon conformity", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to check lexicon conformity",
        ) from exc
