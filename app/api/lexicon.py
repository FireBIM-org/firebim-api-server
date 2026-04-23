from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.lexicon.service import LexiconService
from app.schemas.lexicon import (
    LexiconConformityCheckRequest,
    LexiconConformityCheckResponse,
    LexiconHealthResponse,
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
