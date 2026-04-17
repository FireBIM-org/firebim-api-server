from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.checks.schema_service import extract_graph_schema_payload
from app.core.config import settings
from app.schemas.graphs import (
    GraphDeleteResponse,
    GraphFetchResponse,
    GraphListItem,
    GraphListResponse,
    GraphPublishRequest,
    GraphPublishResponse,
    GraphSchemaExtractRequest,
    GraphSchemaExtractResponse,
    GraphsHealthResponse,
)
from app.storage.building_repository import (
    BUILDINGS_DATASET,
    BuildingRepository,
    default_building_graph_uri,
)
from app.storage.graph_store import GraphStoreError, GraphStoreNotFoundError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graphs", tags=["graphs"])

repo = BuildingRepository()


@router.get("/health", response_model=GraphsHealthResponse)
def graphs_health() -> GraphsHealthResponse:
    connected = repo.health()
    return GraphsHealthResponse(
        status="ok" if connected else "degraded",
        storage_connected=connected,
        dataset=BUILDINGS_DATASET,
        storage_backend="s3",
        storage_host=settings.S3_ENDPOINT_URL,
    )


@router.get("", response_model=GraphListResponse)
def list_graphs() -> GraphListResponse:
    try:
        items = repo.list_graphs()
        graph_items = [GraphListItem(**item) for item in items]
        return GraphListResponse(
            count=len(graph_items),
            graph_uris=[item.graph_uri for item in graph_items],
            graphs=graph_items,
        )
    except GraphStoreError as exc:
        logger.exception("Failed to list building graphs", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.post("/publish", response_model=GraphPublishResponse)
def publish_graph(payload: GraphPublishRequest) -> GraphPublishResponse:
    try:
        graph_uri, overwritten = repo.publish_graph(
            graph_id=payload.graph_id,
            ttl=payload.ttl,
        )
        return GraphPublishResponse(
            success=True,
            graph_id=payload.graph_id,
            graph_uri=graph_uri,
            dataset=BUILDINGS_DATASET,
            overwritten=overwritten,
            message="Building graph published successfully",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to publish building graph", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.delete("/{graph_id}", response_model=GraphDeleteResponse)
def delete_graph(graph_id: str) -> GraphDeleteResponse:
    try:
        graph_uri = repo.delete_graph(graph_id)
        return GraphDeleteResponse(
            success=True,
            graph_id=graph_id,
            graph_uri=graph_uri,
            dataset=BUILDINGS_DATASET,
            message="Building graph deleted successfully",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to delete building graph", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.get("/{graph_id}", response_model=GraphFetchResponse)
def fetch_graph(graph_id: str) -> GraphFetchResponse:
    try:
        graph_uri, ttl = repo.fetch_graph(graph_id)
        return GraphFetchResponse(
            success=True,
            graph_id=graph_id,
            graph_uri=graph_uri,
            ttl=ttl,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to fetch building graph", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.post("/extract-schema", response_model=GraphSchemaExtractResponse)
def extract_graph_schema(payload: GraphSchemaExtractRequest) -> GraphSchemaExtractResponse:
    try:
        graph_uri, ttl = repo.fetch_graph(payload.graph_id)
        extracted = extract_graph_schema_payload(
            graph_id=payload.graph_id,
            graph_uri=graph_uri,
            graph_ttl=ttl,
            include_examples=payload.include_examples,
            max_examples_per_edge=payload.max_examples_per_edge,
        )
        payload_data = extracted["payload"]
        return GraphSchemaExtractResponse(
            success=True,
            graph_id=payload.graph_id,
            graph_uri=graph_uri,
            triple_count=payload_data["triple_count"],
            class_counts=payload_data["class_counts"],
            property_counts=payload_data["property_counts"],
            class_to_class_edges=payload_data["class_to_class_edges"],
            forward_predicates_by_class_pair=payload_data["forward_predicates_by_class_pair"],
            reverse_predicates_by_class_pair=payload_data["reverse_predicates_by_class_pair"],
            warnings=payload_data["warnings"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except GraphStoreError as exc:
        logger.exception("Failed to extract graph schema", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


# Optional helper for consistency with checks engine and future reuse
__all__ = [
    "router",
    "default_building_graph_uri",
]
