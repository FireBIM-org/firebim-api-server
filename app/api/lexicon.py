from __future__ import annotations

import json
import logging
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from app.auth.jwt import get_current_active_user
from app.core.observability import is_request_profiling_enabled, log_event, record_metric
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
    LexiconProfileCreateRequest,
    LexiconProfileDeleteResponse,
    LexiconProfileDeriveRequest,
    LexiconProfileDeriveResponse,
    LexiconProfileGraphCoverageRequest,
    LexiconProfileGraphCoverageResponse,
    LexiconProfileListResponse,
    LexiconProfileMappingCandidatesResponse,
    LexiconProfileResponse,
    LexiconProfileRulesCoverageRequest,
    LexiconProfileRulesCoverageResponse,
    LexiconProfileSummaryResponse,
    LexiconProfileUpdateRequest,
    LexiconReconstructionRequest,
    LexiconReconstructionResponse,
    LexiconRuleOntologySimilarityRequest,
    LexiconRuleOntologySimilarityResponse,
    LexiconRuleTermValidationRequest,
    LexiconRuleTermValidationResponse,
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
from app.storage.lexicon_profile_repository import (
    LexiconProfileNotFoundError,
    LexiconProfileRepository,
    LexiconProfileRepositoryError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lexicon", tags=["lexicon"])

service = LexiconService()
mapping_repo = LexiconMappingRepository()
handshake_repo = LexiconHandshakeRepository()
profile_repo = LexiconProfileRepository()


async def _parse_request_model[ModelT: BaseModel](
    request: Request,
    model_type: type[ModelT],
    *,
    metric_prefix: str,
) -> ModelT:
    body_started_at = monotonic()
    raw_body = await request.body()
    body_read_ms = round((monotonic() - body_started_at) * 1000, 3)
    record_metric(f"{metric_prefix}.body_read_ms", body_read_ms)
    record_metric(f"{metric_prefix}.body_bytes", len(raw_body))

    json_started_at = monotonic()
    try:
        payload_data = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON body: {exc.msg}") from exc
    json_parse_ms = round((monotonic() - json_started_at) * 1000, 3)
    record_metric(f"{metric_prefix}.body_json_parse_ms", json_parse_ms)

    validation_started_at = monotonic()
    try:
        payload = model_type.model_validate(payload_data)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    validation_ms = round((monotonic() - validation_started_at) * 1000, 3)
    record_metric(f"{metric_prefix}.body_model_validate_ms", validation_ms)
    return payload


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


@router.post("/catalog/rule-ontology-similarity", response_model=LexiconRuleOntologySimilarityResponse)
def score_rule_ontology_similarity(
    payload: LexiconRuleOntologySimilarityRequest,
) -> LexiconRuleOntologySimilarityResponse:
    try:
        return service.score_rule_ontology_similarity(payload)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to score rule ontology similarity", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to score rule ontology similarity",
        ) from exc


@router.post("/catalog/rule-term-validation", response_model=LexiconRuleTermValidationResponse)
def validate_rule_terms(payload: LexiconRuleTermValidationRequest) -> LexiconRuleTermValidationResponse:
    """Validate rule target classes and property paths against the selected ontologies."""
    try:
        return service.validate_rule_terms(payload)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to validate rule terms against lexicon catalogs", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to validate rule terms against lexicon catalogs",
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
        ontology, profile, _rule_profiles, _rule_ids = service.derive_profile(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            selected_ontologies=payload.selected_ontologies,
            canonical_target_ontology=payload.canonical_target_ontology,
            rule_id=payload.rule_id,
            rule_ids=[],
            rule=payload.rule,
            rules=[],
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
        ontology, profile, rule_profiles, rule_ids = service.derive_profile(
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
            selected_ontologies=payload.selected_ontologies,
            canonical_target_ontology=payload.canonical_target_ontology,
            rule_id=payload.rule_id,
            rule_ids=payload.rule_ids,
            rule=payload.rule,
            rules=payload.rules,
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
            rule_ids=rule_ids,
            rule_profiles=rule_profiles,
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
            rule_ids=payload.rule_ids,
            rule=payload.rule,
            rules=payload.rules,
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


@router.post("/profiles", response_model=LexiconProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    request: Request,
    current_user: User = Depends(get_current_active_user),
) -> LexiconProfileResponse:
    try:
        payload = await _parse_request_model(
            request,
            LexiconProfileCreateRequest,
            metric_prefix="lexicon.profile_create",
        )
        logic_started_at = monotonic()
        if payload.profile is not None:
            selection_started_at = monotonic()
            selected = service.resolve_selected_ontologies(
                ontology_family=payload.ontology_family,
                country=payload.country,
                version=payload.version,
                selected_ontologies=payload.selected_ontologies,
                canonical_target_ontology=payload.canonical_target_ontology,
            )
            selection_duration_ms = round((monotonic() - selection_started_at) * 1000, 3)
            record_metric("lexicon.profile_create.resolve_selected_ontologies_ms", selection_duration_ms)
            canonical = selected["canonical_target_ontology"]

            write_started_at = monotonic()
            profile_record = profile_repo.create_profile(
                payload=payload,
                user=current_user,
                canonical_family_uri=canonical.canonical_family_uri or "",
                canonical_country_uri=canonical.canonical_country_uri or "",
                rule_ids=payload.rule_ids,
                profile=payload.profile,
                rule_profiles=payload.rule_profiles or [payload.profile],
                selected_ontologies=selected["selected_ontologies"],
                canonical_target_ontology=selected["canonical_target_ontology"],
            )
            record_metric(
                "lexicon.profile_create.persist_ms",
                round((monotonic() - write_started_at) * 1000, 3),
            )
        else:
            derive_started_at = monotonic()
            ontology, profile, rule_profiles, rule_ids = service.derive_profile(
                ontology_family=payload.ontology_family,
                country=payload.country,
                version=payload.version,
                selected_ontologies=payload.selected_ontologies,
                canonical_target_ontology=payload.canonical_target_ontology,
                rule_id=payload.rule_id,
                rule_ids=payload.rule_ids,
                rule=payload.rule,
                rules=payload.rules,
            )
            record_metric("lexicon.profile_create.derive_ms", round((monotonic() - derive_started_at) * 1000, 3))

            selection_started_at = monotonic()
            selected = service.resolve_selected_ontologies(
                ontology_family=payload.ontology_family,
                country=payload.country,
                version=payload.version,
                selected_ontologies=payload.selected_ontologies,
                canonical_target_ontology=payload.canonical_target_ontology,
            )
            record_metric(
                "lexicon.profile_create.resolve_selected_ontologies_ms",
                round((monotonic() - selection_started_at) * 1000, 3),
            )

            write_started_at = monotonic()
            profile_record = profile_repo.create_profile(
                payload=payload,
                user=current_user,
                canonical_family_uri=ontology.canonical_family_uri,
                canonical_country_uri=ontology.canonical_country_uri,
                rule_ids=rule_ids,
                profile=profile,
                rule_profiles=rule_profiles,
                selected_ontologies=selected["selected_ontologies"],
                canonical_target_ontology=selected["canonical_target_ontology"],
            )
            record_metric(
                "lexicon.profile_create.persist_ms",
                round((monotonic() - write_started_at) * 1000, 3),
            )
        logic_duration_ms = round((monotonic() - logic_started_at) * 1000, 3)
        record_metric("lexicon.profile_create.logic_ms", logic_duration_ms)
        request.state.handler_completed_at = monotonic()
        if is_request_profiling_enabled():
            log_event(
                logger,
                "lexicon.profile_create.completed",
                has_explicit_profile=payload.profile is not None,
                selected_ontology_count=len(profile_record.selected_ontologies),
                rule_count=len(profile_record.rule_ids),
                duration_ms=logic_duration_ms,
            )
        return LexiconProfileResponse(profile_record=profile_record)
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconProfileRepositoryError as exc:
        logger.exception("Failed to create lexicon profile", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/profiles", response_model=LexiconProfileListResponse)
def list_profiles(
    search: str | None = Query(default=None),
    ontology_family: str | None = Query(default=None),
    country: str | None = Query(default=None),
    rule_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_active_user),
) -> LexiconProfileListResponse:
    try:
        profiles = profile_repo.list_profiles(current_user)
        if search:
            needle = search.strip().lower()
            profiles = [
                item
                for item in profiles
                if needle in item.name.lower()
                or needle in (item.description or "").lower()
                or any(needle in keyword.lower() for keyword in item.keywords)
                or any(needle in current_rule_id.lower() for current_rule_id in item.rule_ids)
            ]
        if ontology_family:
            profiles = [item for item in profiles if item.ontology_family == ontology_family]
        if country:
            profiles = [item for item in profiles if item.country == country]
        if rule_id:
            profiles = [item for item in profiles if rule_id in item.rule_ids]
        return LexiconProfileListResponse(count=len(profiles), profiles=profiles)
    except LexiconProfileRepositoryError as exc:
        logger.exception("Failed to list lexicon profiles", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/profiles/{profile_id}", response_model=LexiconProfileResponse)
def get_profile(profile_id: str, current_user: User = Depends(get_current_active_user)) -> LexiconProfileResponse:
    try:
        profile_record = profile_repo.get_profile(profile_id, current_user)
        return LexiconProfileResponse(profile_record=profile_record)
    except LexiconProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconProfileRepositoryError as exc:
        logger.exception("Failed to fetch lexicon profile", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.put("/profiles/{profile_id}", response_model=LexiconProfileResponse)
async def update_profile(
    profile_id: str,
    request: Request,
    current_user: User = Depends(get_current_active_user),
) -> LexiconProfileResponse:
    try:
        payload = await _parse_request_model(
            request,
            LexiconProfileUpdateRequest,
            metric_prefix="lexicon.profile_update",
        )
        logic_started_at = monotonic()
        read_started_at = monotonic()
        existing = profile_repo.get_profile(profile_id, current_user)
        record_metric("lexicon.profile_update.read_existing_ms", round((monotonic() - read_started_at) * 1000, 3))
        merge_started_at = monotonic()
        updated = existing.model_copy(
            update={
                "name": payload.name if payload.name is not None else existing.name,
                "description": payload.description if payload.description is not None else existing.description,
                "keywords": payload.keywords if payload.keywords is not None else existing.keywords,
                "selected_ontologies": (
                    payload.selected_ontologies
                    if payload.selected_ontologies is not None
                    else existing.selected_ontologies
                ),
                "canonical_target_ontology": (
                    payload.canonical_target_ontology
                    if payload.canonical_target_ontology is not None
                    else existing.canonical_target_ontology
                ),
                "rule_ids": payload.rule_ids if payload.rule_ids is not None else existing.rule_ids,
                "profile": payload.profile if payload.profile is not None else existing.profile,
                "rule_profiles": payload.rule_profiles if payload.rule_profiles is not None else existing.rule_profiles,
            }
        )
        record_metric("lexicon.profile_update.merge_ms", round((monotonic() - merge_started_at) * 1000, 3))
        write_started_at = monotonic()
        profile_record = profile_repo.update_profile(profile_id=profile_id, user=current_user, updated=updated)
        record_metric("lexicon.profile_update.persist_ms", round((monotonic() - write_started_at) * 1000, 3))
        logic_duration_ms = round((monotonic() - logic_started_at) * 1000, 3)
        record_metric("lexicon.profile_update.logic_ms", logic_duration_ms)
        request.state.handler_completed_at = monotonic()
        if is_request_profiling_enabled():
            log_event(
                logger,
                "lexicon.profile_update.completed",
                profile_id=profile_id,
                selected_ontology_count=len(profile_record.selected_ontologies),
                rule_count=len(profile_record.rule_ids),
                duration_ms=logic_duration_ms,
            )
        return LexiconProfileResponse(profile_record=profile_record)
    except LexiconProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconProfileRepositoryError as exc:
        logger.exception("Failed to update lexicon profile", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.delete("/profiles/{profile_id}", response_model=LexiconProfileDeleteResponse)
async def delete_profile(
    profile_id: str,
    request: Request,
    current_user: User = Depends(get_current_active_user),
) -> LexiconProfileDeleteResponse:
    try:
        delete_started_at = monotonic()
        profile_repo.delete_profile(profile_id, current_user)
        delete_duration_ms = round((monotonic() - delete_started_at) * 1000, 3)
        record_metric("lexicon.profile_delete.logic_ms", delete_duration_ms)
        request.state.handler_completed_at = monotonic()
        if is_request_profiling_enabled():
            log_event(logger, "lexicon.profile_delete.completed", profile_id=profile_id, duration_ms=delete_duration_ms)
        return LexiconProfileDeleteResponse(deleted=True, profile_id=profile_id)
    except LexiconProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconProfileRepositoryError as exc:
        logger.exception("Failed to delete lexicon profile", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/profiles/{profile_id}/rules/coverage", response_model=LexiconProfileRulesCoverageResponse)
def profile_rules_coverage(
    profile_id: str,
    payload: LexiconProfileRulesCoverageRequest,
    current_user: User = Depends(get_current_active_user),
) -> LexiconProfileRulesCoverageResponse:
    try:
        profile_record = profile_repo.get_profile(profile_id, current_user)
        return service.compute_profile_rules_coverage(
            profile_id=profile_id,
            base_profile=profile_record.profile,
            ontology_family=profile_record.ontology_family,
            country=profile_record.country,
            version=profile_record.version,
            selected_ontologies=profile_record.selected_ontologies,
            canonical_target_ontology=profile_record.canonical_target_ontology,
            rule_id=payload.rule_id,
            rule_ids=payload.rule_ids,
            rule=payload.rule,
            rules=payload.rules,
            default_rule_ids=profile_record.rule_ids,
            stored_rule_profiles=profile_record.rule_profiles,
        )
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconProfileRepositoryError as exc:
        logger.exception("Failed to calculate lexicon profile rules coverage", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/profiles/{profile_id}/graph-coverage", response_model=LexiconProfileGraphCoverageResponse)
def profile_graph_coverage(
    profile_id: str,
    payload: LexiconProfileGraphCoverageRequest,
    current_user: User = Depends(get_current_active_user),
) -> LexiconProfileGraphCoverageResponse:
    try:
        profile_record = profile_repo.get_profile(profile_id, current_user)
        return service.compute_profile_graph_coverage(
            profile_id=profile_id,
            graph_id=payload.graph_id,
            selected_ontologies=profile_record.selected_ontologies,
            expected_profile=profile_record.profile,
        )
    except (GraphStoreError, GraphStoreNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LexiconProfileRepositoryError as exc:
        logger.exception("Failed to calculate lexicon profile graph coverage", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/profiles/{profile_id}/mappings/candidates", response_model=LexiconProfileMappingCandidatesResponse)
def profile_mapping_candidates(
    profile_id: str,
    current_user: User = Depends(get_current_active_user),
) -> LexiconProfileMappingCandidatesResponse:
    try:
        profile_record = profile_repo.get_profile(profile_id, current_user)
        mapping_summaries = mapping_repo.list_mappings(current_user)
        mappings = [mapping_repo.get_mapping(item.id, current_user) for item in mapping_summaries]
        return service.find_profile_mapping_candidates(
            profile_id=profile_id,
            profile=profile_record.profile,
            mappings=mappings,
        )
    except LexiconProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LexiconProfileRepositoryError as exc:
        logger.exception("Failed to find lexicon profile mapping candidates", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except LexiconMappingRepositoryError as exc:
        logger.exception("Failed to load mappings for lexicon profile candidates", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


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
