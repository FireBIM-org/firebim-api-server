from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, PlainTextResponse

from app.auth.jwt import get_current_active_user
from app.core.config import settings
from app.ontologies.service import (
    build_dictionary_conversion,
    build_version_summary_from_metadata,
    build_version_summary_response,
    resolve_candidate_dictionary_json,
    validation_report_model,
)
from app.ontologies.service import (
    fetch_ontology_version as fetch_ontology_version_data,
)
from app.schemas.ontologies import (
    OntologyDeleteResponse,
    OntologyDictionaryCandidateCreateRequest,
    OntologyDictionaryCandidateListResponse,
    OntologyDictionaryCandidatePublishRequest,
    OntologyDictionaryCandidateResponse,
    OntologyDictionaryCandidateSummaryResponse,
    OntologyDictionaryConversionRequest,
    OntologyDictionaryConversionResponse,
    OntologyDictionaryValidationReport,
    OntologyHealthResponse,
    OntologyPublishRequest,
    OntologyPublishResponse,
    OntologySummaryResponse,
    OntologyVersionFetchResponse,
    OntologyVersionListResponse,
    OntologyVersionSummaryResponse,
)
from app.storage.graph_store import GraphStoreError, GraphStoreNotFoundError
from app.storage.ontology_candidate_repository import (
    OntologyCandidateNotFoundError,
    OntologyCandidateRepository,
    OntologyCandidateRepositoryError,
)
from app.storage.ontology_repository import ONTOLOGIES_DATASET, OntologyRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ontologies", tags=["ontologies"])

repo = OntologyRepository()
candidate_repo = OntologyCandidateRepository()


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


@router.post(
    "/conversions/dictionary-json-to-ttl",
    response_model=OntologyDictionaryConversionResponse,
)
def convert_dictionary_json_to_ttl(
    payload: OntologyDictionaryConversionRequest,
    _current_user=Depends(get_current_active_user),
) -> OntologyDictionaryConversionResponse:
    try:
        conversion = build_dictionary_conversion(repo, payload)
        saved_resource_filename = None
        if payload.output_resource_filename:
            repo.save_local_resource(payload.output_resource_filename, conversion.ttl)
            saved_resource_filename = payload.output_resource_filename

        publish_result = None
        if payload.publish:
            if payload.issued_at is None:
                raise ValueError("issued_at is required when publish=true")
            published = repo.publish_version(
                OntologyPublishRequest(
                    ontology_family=payload.ontology_family,
                    country=payload.country,
                    version=payload.version,
                    status=payload.status,
                    issued_at=payload.issued_at,
                    ttl=conversion.ttl,
                    resource_filename=saved_resource_filename,
                    default_version=payload.default_version,
                )
            )
            publish_result = OntologyPublishResponse(success=True, **published)

        return OntologyDictionaryConversionResponse(
            ontology_family=conversion.ontology_family,
            country=conversion.country,
            version=conversion.version,
            source_dictionary_name=conversion.source_dictionary_name,
            source_dictionary_code=conversion.source_dictionary_code,
            source_dictionary_version=conversion.source_dictionary_version,
            source_resource_filename=payload.source_resource_filename,
            output_resource_filename=saved_resource_filename,
            ontology_uri=conversion.ontology_uri,
            version_iri=conversion.version_iri,
            class_count=conversion.class_count,
            property_count=conversion.property_count,
            datatype_property_count=conversion.datatype_property_count,
            object_property_count=conversion.object_property_count,
            subclass_count=conversion.subclass_count,
            class_property_link_count=conversion.class_property_link_count,
            ttl=conversion.ttl,
            validation_report=validation_report_model(conversion.validation_report),
            published=publish_result is not None,
            publish_result=publish_result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to convert dictionary JSON to ontology TTL", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/candidates/dictionaries",
    response_model=OntologyDictionaryCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_dictionary_candidate(
    payload: OntologyDictionaryCandidateCreateRequest,
    current_user=Depends(get_current_active_user),
) -> OntologyDictionaryCandidateResponse:
    try:
        dictionary_json = resolve_candidate_dictionary_json(repo, payload)
        candidate = candidate_repo.create_candidate(payload, dictionary_json=dictionary_json, user=current_user)
        return OntologyDictionaryCandidateResponse(candidate=candidate)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyCandidateRepositoryError as exc:
        logger.exception("Failed to create ontology dictionary candidate", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/candidates/dictionaries", response_model=OntologyDictionaryCandidateListResponse)
def list_dictionary_candidates(
    current_user=Depends(get_current_active_user),
) -> OntologyDictionaryCandidateListResponse:
    try:
        candidates = candidate_repo.list_candidates(current_user)
        return OntologyDictionaryCandidateListResponse(count=len(candidates), candidates=candidates)
    except OntologyCandidateRepositoryError as exc:
        logger.exception("Failed to list ontology dictionary candidates", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/candidates/dictionaries/{candidate_id}", response_model=OntologyDictionaryCandidateResponse)
def get_dictionary_candidate(
    candidate_id: str,
    current_user=Depends(get_current_active_user),
) -> OntologyDictionaryCandidateResponse:
    try:
        candidate = candidate_repo.get_candidate(candidate_id, current_user)
        return OntologyDictionaryCandidateResponse(candidate=candidate)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyCandidateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyCandidateRepositoryError as exc:
        logger.exception("Failed to fetch ontology dictionary candidate", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get(
    "/candidates/dictionaries/{candidate_id}/summary",
    response_model=OntologyDictionaryCandidateSummaryResponse,
)
def get_dictionary_candidate_summary(
    candidate_id: str,
    current_user=Depends(get_current_active_user),
) -> OntologyDictionaryCandidateSummaryResponse:
    try:
        candidate = candidate_repo.get_candidate(candidate_id, current_user)
        report = candidate.last_validation_report
        warnings = list(report.issues) if report is not None else []
        return OntologyDictionaryCandidateSummaryResponse(
            id=candidate.id,
            name=candidate.name,
            ontology_family=candidate.ontology_family,
            country=candidate.country,
            version=candidate.version,
            source_dictionary_name=candidate.source_dictionary_name,
            source_dictionary_code=candidate.source_dictionary_code,
            source_dictionary_version=candidate.source_dictionary_version,
            class_count=report.class_count if report else None,
            subclass_count=report.subclass_count if report else None,
            property_count=report.property_count if report else None,
            datatype_property_count=report.datatype_property_count if report else None,
            object_property_count=report.object_property_count if report else None,
            class_property_link_count=report.class_property_link_count if report else None,
            valid=report.valid if report else None,
            completeness_ok=report.completeness_ok if report else None,
            ttl_form_ok=report.ttl_form_ok if report else None,
            publish_ready=bool(report and report.valid and report.completeness_ok and report.ttl_form_ok),
            warnings=warnings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyCandidateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyCandidateRepositoryError as exc:
        logger.exception("Failed to summarize ontology dictionary candidate", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/candidates/dictionaries/{candidate_id}/json")
def download_dictionary_candidate_json(
    candidate_id: str,
    current_user=Depends(get_current_active_user),
) -> JSONResponse:
    try:
        candidate = candidate_repo.get_candidate(candidate_id, current_user)
        return JSONResponse(candidate.dictionary_json)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyCandidateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyCandidateRepositoryError as exc:
        logger.exception("Failed to download ontology dictionary candidate JSON", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/candidates/dictionaries/{candidate_id}/validate",
    response_model=OntologyDictionaryValidationReport,
)
def validate_dictionary_candidate(
    candidate_id: str,
    current_user=Depends(get_current_active_user),
) -> OntologyDictionaryValidationReport:
    try:
        candidate = candidate_repo.get_candidate(candidate_id, current_user)
        conversion = build_dictionary_conversion(
            repo,
            OntologyDictionaryConversionRequest(
                ontology_family=candidate.ontology_family,
                country=candidate.country,
                version=candidate.version,
                dictionary_json=candidate.dictionary_json,
            ),
        )
        validation_report = validation_report_model(conversion.validation_report)
        candidate_repo.update_validation_report(candidate_id, validation_report, current_user)
        return validation_report
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyCandidateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyCandidateRepositoryError as exc:
        logger.exception("Failed to validate ontology dictionary candidate", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/candidates/dictionaries/{candidate_id}/convert",
    response_model=OntologyDictionaryConversionResponse,
)
def convert_dictionary_candidate(
    candidate_id: str,
    current_user=Depends(get_current_active_user),
) -> OntologyDictionaryConversionResponse:
    try:
        candidate = candidate_repo.get_candidate(candidate_id, current_user)
        conversion = build_dictionary_conversion(
            repo,
            OntologyDictionaryConversionRequest(
                ontology_family=candidate.ontology_family,
                country=candidate.country,
                version=candidate.version,
                dictionary_json=candidate.dictionary_json,
            ),
        )
        validation_report = validation_report_model(conversion.validation_report)
        candidate_repo.update_validation_report(candidate_id, validation_report, current_user)
        return OntologyDictionaryConversionResponse(
            ontology_family=conversion.ontology_family,
            country=conversion.country,
            version=conversion.version,
            source_dictionary_name=conversion.source_dictionary_name,
            source_dictionary_code=conversion.source_dictionary_code,
            source_dictionary_version=conversion.source_dictionary_version,
            source_resource_filename=candidate.source_resource_filename,
            ontology_uri=conversion.ontology_uri,
            version_iri=conversion.version_iri,
            class_count=conversion.class_count,
            property_count=conversion.property_count,
            datatype_property_count=conversion.datatype_property_count,
            object_property_count=conversion.object_property_count,
            subclass_count=conversion.subclass_count,
            class_property_link_count=conversion.class_property_link_count,
            ttl=conversion.ttl,
            validation_report=validation_report,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyCandidateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyCandidateRepositoryError as exc:
        logger.exception("Failed to convert ontology dictionary candidate", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/candidates/dictionaries/{candidate_id}/publish",
    response_model=OntologyDictionaryConversionResponse,
)
def publish_dictionary_candidate(
    candidate_id: str,
    payload: OntologyDictionaryCandidatePublishRequest,
    current_user=Depends(get_current_active_user),
) -> OntologyDictionaryConversionResponse:
    try:
        candidate = candidate_repo.get_candidate(candidate_id, current_user)
        conversion = build_dictionary_conversion(
            repo,
            OntologyDictionaryConversionRequest(
                ontology_family=candidate.ontology_family,
                country=candidate.country,
                version=candidate.version,
                dictionary_json=candidate.dictionary_json,
            ),
        )
        validation_report = validation_report_model(conversion.validation_report)
        candidate_repo.update_validation_report(candidate_id, validation_report, current_user)

        saved_resource_filename = None
        if payload.output_resource_filename:
            repo.save_local_resource(payload.output_resource_filename, conversion.ttl)
            saved_resource_filename = payload.output_resource_filename

        published = repo.publish_version(
            OntologyPublishRequest(
                ontology_family=candidate.ontology_family,
                country=candidate.country,
                version=candidate.version,
                status=payload.status,
                issued_at=payload.issued_at,
                ttl=conversion.ttl,
                resource_filename=saved_resource_filename,
                default_version=payload.default_version,
            )
        )
        return OntologyDictionaryConversionResponse(
            ontology_family=conversion.ontology_family,
            country=conversion.country,
            version=conversion.version,
            source_dictionary_name=conversion.source_dictionary_name,
            source_dictionary_code=conversion.source_dictionary_code,
            source_dictionary_version=conversion.source_dictionary_version,
            source_resource_filename=candidate.source_resource_filename,
            output_resource_filename=saved_resource_filename,
            ontology_uri=conversion.ontology_uri,
            version_iri=conversion.version_iri,
            class_count=conversion.class_count,
            property_count=conversion.property_count,
            datatype_property_count=conversion.datatype_property_count,
            object_property_count=conversion.object_property_count,
            subclass_count=conversion.subclass_count,
            class_property_link_count=conversion.class_property_link_count,
            ttl=conversion.ttl,
            validation_report=validation_report,
            published=True,
            publish_result=OntologyPublishResponse(success=True, **published),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyCandidateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyCandidateRepositoryError as exc:
        logger.exception("Failed to publish ontology dictionary candidate", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to publish ontology dictionary candidate", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.delete("/candidates/dictionaries/{candidate_id}")
def delete_dictionary_candidate(
    candidate_id: str,
    current_user=Depends(get_current_active_user),
) -> dict[str, object]:
    try:
        candidate_repo.delete_candidate(candidate_id, current_user)
        return {"deleted": True, "candidate_id": candidate_id}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OntologyCandidateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OntologyCandidateRepositoryError as exc:
        logger.exception("Failed to delete ontology dictionary candidate", exc_info=exc)
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
    "/{ontology_family}/{country}/versions/{version}/summary",
    response_model=OntologyVersionSummaryResponse,
)
def fetch_ontology_version_summary(
    ontology_family: str,
    country: str,
    version: str,
) -> OntologyVersionSummaryResponse:
    ontology_family = ontology_family.strip().lower()
    country = country.strip().lower()
    version = version.strip()
    try:
        metadata = repo.fetch_version_metadata(ontology_family, country, version)
        if any(
            (
                metadata.class_count,
                metadata.property_count,
                metadata.property_set_count,
                metadata.subclass_count,
                metadata.class_property_link_count,
            )
        ):
            return build_version_summary_from_metadata(
                ontology_family=ontology_family,
                country=country,
                metadata=metadata,
            )
        response = fetch_ontology_version_data(
            repo,
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        return build_version_summary_response(response)
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to fetch ontology version summary", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get(
    "/{ontology_family}/{country}/versions/{version}/ttl",
    response_class=PlainTextResponse,
    responses={200: {"description": "Raw ontology Turtle"}},
)
def fetch_ontology_ttl(ontology_family: str, country: str, version: str) -> PlainTextResponse:
    response = _fetch_ontology_version(ontology_family, country, version)
    return PlainTextResponse(response.ttl, media_type="text/turtle; charset=utf-8")


def _fetch_ontology_version(ontology_family: str, country: str, version: str) -> OntologyVersionFetchResponse:
    try:
        return fetch_ontology_version_data(
            repo,
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to fetch ontology version", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
