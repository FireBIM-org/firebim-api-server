from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.schemas.graphs import (
    GraphDeleteResponse,
    GraphListItem,
    GraphListResponse,
    GraphPublishRequest,
    GraphPublishResponse,
    GraphsHealthResponse,
)
from app.storage.building_repository import (
    BUILDINGS_DATASET,
    BuildingRepository,
    default_building_graph_uri,
)
from app.storage.fuseki_client import FusekiError, FusekiNotFoundError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graphs", tags=["graphs"])

repo = BuildingRepository()


@router.get("/health", response_model=GraphsHealthResponse)
def graphs_health() -> GraphsHealthResponse:
    connected = repo.health()
    return GraphsHealthResponse(
        status="ok" if connected else "degraded",
        fuseki_connected=connected,
        dataset=BUILDINGS_DATASET,
        fuseki_host=settings.FUSEKI_HOST,
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
    except FusekiError as exc:
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
    except FusekiError as exc:
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
    except FusekiNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except FusekiError as exc:
        logger.exception("Failed to delete building graph", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


# Optional helper for consistency with checks engine and future reuse
__all__ = [
    "router",
    "default_building_graph_uri",
]