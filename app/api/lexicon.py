from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.jwt import get_current_active_user
from app.lexicon.service import LexiconService
from app.models.models import User
from app.schemas.lexicon import (
    LexiconCatalogCompareRequest,
    LexiconCatalogCompareResponse,
    LexiconCatalogRelatedTermsResponse,
    LexiconCatalogResolveRequest,
    LexiconCatalogResolveResponse,
    LexiconCatalogSimilarityMatrixRequest,
    LexiconCatalogSimilarityMatrixResponse,
    LexiconCatalogSimilarityRequest,
    LexiconCatalogSimilarityResponse,
    LexiconCatalogTermResponse,
    LexiconCatalogTermsResponse,
    LexiconConformityCheckRequest,
    LexiconConformityCheckResponse,
    LexiconGraphEvidenceSummaryRequest,
    LexiconGraphEvidenceSummaryResponse,
    LexiconHandshakeAcceptRequest,
    LexiconHandshakeConformityRequest,
    LexiconHandshakeCreateRequest,
    LexiconHandshakeReconstructionRequest,
    LexiconHandshakeResponse,
    LexiconHealthResponse,
    LexiconMappingCreateRequest,
    LexiconMappingDeleteResponse,
    LexiconMappingEvaluateRequest,
    LexiconMappingEvaluateResponse,
    LexiconMappingListResponse,
    LexiconMappingResponse,
    LexiconMappingValidationRequest,
    LexiconMappingValidationResponse,
    LexiconNextNeededResponse,
    LexiconProfileDeriveRequest,
    LexiconProfileDeriveResponse,
    LexiconProfileSummaryResponse,
    LexiconReconstructionRequest,
    LexiconReconstructionResponse,
)
from app.storage.graph_store import GraphStoreError, GraphStoreNotFoundError
from app.storage.lexicon_handshake_repository import (
    LexiconHandshakeNotFoundError,
    LexiconHandshakeRepository,
    LexiconHandshakeRepositoryError,
)
from app.storage.lexicon_mapping_repository import (
    LexiconMappingNotFoundError,
    LexiconMappingRepository,
    LexiconMappingRepositoryError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lexicon", tags=["lexicon"])

service = LexiconService()
mapping_repo = LexiconMappingRepository()
handshake_repo = LexiconHandshakeRepository()


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
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    parent_term_id: str | None = Query(default=None),
    property_of: str | None = Query(default=None),
    is_predefined_type: bool | None = Query(default=None),
) -> LexiconCatalogTermsResponse:
    try:
        return service.list_catalog_terms(
            ontology_family=ontology_family,
            country=country,
            version=version,
            kind=kind,
            search=search,
            limit=limit,
            cursor=cursor,
            parent_term_id=parent_term_id,
            property_of=property_of,
            is_predefined_type=is_predefined_type,
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


@router.post("/catalog/resolve-terms", response_model=LexiconCatalogResolveResponse)
def resolve_catalog_terms(payload: LexiconCatalogResolveRequest) -> LexiconCatalogResolveResponse:
    try:
        return service.resolve_catalog_terms(payload)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to resolve lexicon catalog terms", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to resolve lexicon catalog terms",
        ) from exc


@router.post("/catalog/compare", response_model=LexiconCatalogCompareResponse)
def compare_catalog_terms(payload: LexiconCatalogCompareRequest) -> LexiconCatalogCompareResponse:
    try:
        return service.compare_catalog_terms(payload)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to compare lexicon catalog terms", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to compare lexicon catalog terms",
        ) from exc


@router.post("/catalog/similarity", response_model=LexiconCatalogSimilarityResponse)
def score_catalog_similarity(payload: LexiconCatalogSimilarityRequest) -> LexiconCatalogSimilarityResponse:
    try:
        return service.score_catalog_similarity(payload)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to score lexicon catalog similarity", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to score lexicon catalog similarity",
        ) from exc


@router.post("/catalog/similarity-matrix", response_model=LexiconCatalogSimilarityMatrixResponse)
def score_catalog_similarity_matrix(
    payload: LexiconCatalogSimilarityMatrixRequest,
) -> LexiconCatalogSimilarityMatrixResponse:
    try:
        return service.score_catalog_similarity_matrix(payload)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to score lexicon similarity matrix", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to score lexicon similarity matrix",
        ) from exc


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/classes/{term_id}/properties",
    response_model=LexiconCatalogRelatedTermsResponse,
)
def list_class_properties(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
) -> LexiconCatalogRelatedTermsResponse:
    return _list_related_catalog_terms(ontology_family, country, version, term_id, "properties")


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/classes/{term_id}/property-sets",
    response_model=LexiconCatalogRelatedTermsResponse,
)
def list_class_property_sets(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
) -> LexiconCatalogRelatedTermsResponse:
    return _list_related_catalog_terms(ontology_family, country, version, term_id, "property_sets")


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/classes/{term_id}/children",
    response_model=LexiconCatalogRelatedTermsResponse,
)
def list_class_children(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
) -> LexiconCatalogRelatedTermsResponse:
    return _list_related_catalog_terms(ontology_family, country, version, term_id, "children")


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/classes/{term_id}/parents",
    response_model=LexiconCatalogRelatedTermsResponse,
)
def list_class_parents(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
) -> LexiconCatalogRelatedTermsResponse:
    return _list_related_catalog_terms(ontology_family, country, version, term_id, "parents")


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/property-sets/{term_id}/properties",
    response_model=LexiconCatalogRelatedTermsResponse,
)
def list_property_set_properties(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
) -> LexiconCatalogRelatedTermsResponse:
    return _list_related_catalog_terms(ontology_family, country, version, term_id, "properties")


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/properties/{term_id}/classes",
    response_model=LexiconCatalogRelatedTermsResponse,
)
def list_property_classes(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
) -> LexiconCatalogRelatedTermsResponse:
    return _list_related_catalog_terms(ontology_family, country, version, term_id, "classes")


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/properties/{term_id}/property-sets",
    response_model=LexiconCatalogRelatedTermsResponse,
)
def list_property_property_sets(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
) -> LexiconCatalogRelatedTermsResponse:
    return _list_related_catalog_terms(ontology_family, country, version, term_id, "property_sets")


@router.get(
    "/catalog/{ontology_family}/{country}/{version}/properties/{term_id}/related-properties",
    response_model=LexiconCatalogRelatedTermsResponse,
)
def list_related_properties(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
) -> LexiconCatalogRelatedTermsResponse:
    return _list_related_catalog_terms(ontology_family, country, version, term_id, "related_properties")


@router.post("/graphs/evidence-summary", response_model=LexiconGraphEvidenceSummaryResponse)
def summarize_graph_evidence(payload: LexiconGraphEvidenceSummaryRequest) -> LexiconGraphEvidenceSummaryResponse:
    try:
        return service.summarize_graph_evidence(payload)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to summarize graph evidence", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to summarize graph evidence",
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


@router.post("/mappings/evaluate", response_model=LexiconMappingEvaluateResponse)
def evaluate_mapping(
    payload: LexiconMappingEvaluateRequest,
    current_user: User = Depends(get_current_active_user),
) -> LexiconMappingEvaluateResponse:
    try:
        mapping_payload = (
            LexiconMappingValidationRequest.model_validate(mapping_repo.get_mapping(payload.mapping_id, current_user))
            if payload.mapping_id
            else payload.mapping
        )
        if mapping_payload is None:
            raise ValueError("mapping payload is required")
        return service.evaluate_mapping(payload=payload, mapping=mapping_payload)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconMappingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconMappingRepositoryError as exc:
        logger.exception("Failed to load lexicon mapping for evaluation", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to evaluate lexicon mapping", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to evaluate lexicon mapping",
        ) from exc


@router.post("/mappings", response_model=LexiconMappingResponse, status_code=status.HTTP_201_CREATED)
def create_mapping(
    payload: LexiconMappingCreateRequest,
    current_user: User = Depends(get_current_active_user),
) -> LexiconMappingResponse:
    try:
        validation = service.validate_mapping(payload=payload)
        if not validation.valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Lexicon mapping is not valid",
                    "errors": validation.errors,
                    "warnings": validation.warnings,
                },
            )
        mapping = mapping_repo.create_mapping(payload, current_user)
        return LexiconMappingResponse(mapping=mapping)
    except HTTPException:
        raise
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconMappingRepositoryError as exc:
        logger.exception("Failed to create lexicon mapping", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/mappings", response_model=LexiconMappingListResponse)
def list_mappings(current_user: User = Depends(get_current_active_user)) -> LexiconMappingListResponse:
    try:
        mappings = mapping_repo.list_mappings(current_user)
        return LexiconMappingListResponse(count=len(mappings), mappings=mappings)
    except LexiconMappingRepositoryError as exc:
        logger.exception("Failed to list lexicon mappings", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/mappings/{mapping_id}", response_model=LexiconMappingResponse)
def get_mapping(mapping_id: str, current_user: User = Depends(get_current_active_user)) -> LexiconMappingResponse:
    try:
        mapping = mapping_repo.get_mapping(mapping_id, current_user)
        return LexiconMappingResponse(mapping=mapping)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconMappingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconMappingRepositoryError as exc:
        logger.exception("Failed to fetch lexicon mapping", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.delete("/mappings/{mapping_id}", response_model=LexiconMappingDeleteResponse)
def delete_mapping(
    mapping_id: str,
    current_user: User = Depends(get_current_active_user),
) -> LexiconMappingDeleteResponse:
    try:
        mapping_repo.delete_mapping(mapping_id, current_user)
        return LexiconMappingDeleteResponse(deleted=True, mapping_id=mapping_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconMappingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconMappingRepositoryError as exc:
        logger.exception("Failed to delete lexicon mapping", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/handshakes", response_model=LexiconHandshakeResponse, status_code=status.HTTP_201_CREATED)
def create_handshake(
    payload: LexiconHandshakeCreateRequest,
    current_user: User = Depends(get_current_active_user),
) -> LexiconHandshakeResponse:
    try:
        ontology, profile = service.derive_profile(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            selected_ontologies=payload.selected_ontologies,
            canonical_target_ontology=payload.canonical_target_ontology,
            rule_id=payload.rule_id,
            rule=payload.rule,
        )
        selected_ontologies = service.resolve_selected_ontologies(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            selected_ontologies=payload.selected_ontologies,
            canonical_target_ontology=payload.canonical_target_ontology,
        )
        canonical_target_ontology = selected_ontologies["canonical_target_ontology"]
        handshake = handshake_repo.create_handshake(
            user=current_user,
            ontology_family=ontology.ontology_family,
            country=ontology.country,
            version=ontology.version,
            canonical_family_uri=ontology.canonical_family_uri,
            canonical_country_uri=ontology.canonical_country_uri,
            selected_ontologies=selected_ontologies["selected_ontologies"],
            canonical_target_ontology=canonical_target_ontology,
            profile=profile,
            accepted_payload=payload.accepted_payload,
        )
        return LexiconHandshakeResponse(handshake=handshake)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconHandshakeRepositoryError as exc:
        logger.exception("Failed to create lexicon handshake", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/handshakes/{handshake_id}", response_model=LexiconHandshakeResponse)
def get_handshake(
    handshake_id: str,
    current_user: User = Depends(get_current_active_user),
) -> LexiconHandshakeResponse:
    try:
        handshake = handshake_repo.get_handshake(handshake_id, current_user)
        return LexiconHandshakeResponse(handshake=handshake)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconHandshakeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconHandshakeRepositoryError as exc:
        logger.exception("Failed to fetch lexicon handshake", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/handshakes/{handshake_id}/accept", response_model=LexiconHandshakeResponse)
def accept_handshake_payload(
    handshake_id: str,
    payload: LexiconHandshakeAcceptRequest,
    current_user: User = Depends(get_current_active_user),
) -> LexiconHandshakeResponse:
    try:
        handshake = handshake_repo.update_accepted_payload(
            handshake_id=handshake_id,
            accepted_payload=payload.accepted_payload,
            user=current_user,
        )
        return LexiconHandshakeResponse(handshake=handshake)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconHandshakeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconHandshakeRepositoryError as exc:
        logger.exception("Failed to accept lexicon handshake payload", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/handshakes/{handshake_id}/next-needed", response_model=LexiconNextNeededResponse)
def get_handshake_next_needed(
    handshake_id: str,
    current_user: User = Depends(get_current_active_user),
) -> LexiconNextNeededResponse:
    try:
        handshake = handshake_repo.get_handshake(handshake_id, current_user)
        return service.compute_next_needed(handshake)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconHandshakeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconHandshakeRepositoryError as exc:
        logger.exception("Failed to calculate lexicon handshake next-needed", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/handshakes/{handshake_id}/reconstruction", response_model=LexiconReconstructionResponse)
def reconstruct_handshake_graph(
    handshake_id: str,
    payload: LexiconHandshakeReconstructionRequest,
    current_user: User = Depends(get_current_active_user),
) -> LexiconReconstructionResponse:
    try:
        handshake = handshake_repo.get_handshake(handshake_id, current_user)
        mapping_ids = set(handshake.accepted_payload.mapping_ids)
        for target in handshake.accepted_payload.targets:
            mapping_ids.update(target.mapping_ids)
        mappings = [mapping_repo.get_mapping(mapping_id, current_user) for mapping_id in sorted(mapping_ids)]
        return service.reconstruct_graph_from_handshake(
            handshake=handshake,
            mappings=mappings,
            graph_id=payload.graph_id,
            instance_base_uri=payload.instance_base_uri,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (LexiconHandshakeNotFoundError, LexiconMappingNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (LexiconHandshakeRepositoryError, LexiconMappingRepositoryError) as exc:
        logger.exception("Failed to reconstruct lexicon handshake graph", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/handshakes/{handshake_id}/conformity", response_model=LexiconConformityCheckResponse)
def check_handshake_conformity(
    handshake_id: str,
    payload: LexiconHandshakeConformityRequest,
    current_user: User = Depends(get_current_active_user),
) -> LexiconConformityCheckResponse:
    try:
        handshake = handshake_repo.get_handshake(handshake_id, current_user)
        mapping_ids = set(handshake.accepted_payload.mapping_ids)
        for target in handshake.accepted_payload.targets:
            mapping_ids.update(target.mapping_ids)
        mappings = [mapping_repo.get_mapping(mapping_id, current_user) for mapping_id in sorted(mapping_ids)]
        reconstruction = service.reconstruct_graph_from_handshake(
            handshake=handshake,
            mappings=mappings,
            graph_id=payload.graph_id,
            instance_base_uri=payload.instance_base_uri,
        )
        return service.check_conformity(
            ontology_family=handshake.ontology_family,
            country=handshake.country,
            version=handshake.version,
            ttl=reconstruction.ttl,
            expected_profile=handshake.profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (LexiconHandshakeNotFoundError, LexiconMappingNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (LexiconHandshakeRepositoryError, LexiconMappingRepositoryError) as exc:
        logger.exception("Failed to check lexicon handshake conformity", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/profiles/derive", response_model=LexiconProfileDeriveResponse)
def derive_execution_profile(payload: LexiconProfileDeriveRequest) -> LexiconProfileDeriveResponse:
    try:
        ontology, profile = service.derive_profile(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            selected_ontologies=payload.selected_ontologies,
            canonical_target_ontology=payload.canonical_target_ontology,
            rule_id=payload.rule_id,
            rule=payload.rule,
        )
        selected_ontologies = service.resolve_selected_ontologies(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            selected_ontologies=payload.selected_ontologies,
            canonical_target_ontology=payload.canonical_target_ontology,
        )
        return LexiconProfileDeriveResponse(
            ontology_family=ontology.ontology_family,
            country=ontology.country,
            version=ontology.version,
            canonical_family_uri=ontology.canonical_family_uri,
            canonical_country_uri=ontology.canonical_country_uri,
            selected_ontologies=selected_ontologies["selected_ontologies"],
            canonical_target_ontology=selected_ontologies["canonical_target_ontology"],
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


@router.post("/profiles/summary", response_model=LexiconProfileSummaryResponse)
def summarize_execution_profile(payload: LexiconProfileDeriveRequest) -> LexiconProfileSummaryResponse:
    try:
        return service.summarize_profile(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            selected_ontologies=payload.selected_ontologies,
            canonical_target_ontology=payload.canonical_target_ontology,
            rule_id=payload.rule_id,
            rule=payload.rule,
        )
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to summarize lexicon execution profile", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to summarize lexicon execution profile",
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


def _list_related_catalog_terms(
    ontology_family: str,
    country: str,
    version: str,
    term_id: str,
    relation: str,
) -> LexiconCatalogRelatedTermsResponse:
    try:
        return service.list_related_catalog_terms(
            ontology_family=ontology_family,
            country=country,
            version=version,
            term_id=term_id,
            relation=relation,
        )
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to list related lexicon catalog terms", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to list related lexicon catalog terms",
        ) from exc
