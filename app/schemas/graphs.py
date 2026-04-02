from __future__ import annotations

from pydantic import BaseModel, Field


class GraphsHealthResponse(BaseModel):
    status: str
    fuseki_connected: bool
    dataset: str
    fuseki_host: str | None = None


class GraphPublishRequest(BaseModel):
    graph_id: str = Field(..., min_length=1, description="Stable graph identifier")
    ttl: str = Field(..., min_length=1, description="Building graph in Turtle format")


class GraphPublishResponse(BaseModel):
    success: bool = True
    graph_id: str
    graph_uri: str
    dataset: str
    overwritten: bool
    message: str


class GraphListItem(BaseModel):
    graph_uri: str
    triple_count: int | None = None


class GraphListResponse(BaseModel):
    count: int
    graph_uris: list[str]
    graphs: list[GraphListItem] = Field(default_factory=list)


class GraphDeleteResponse(BaseModel):
    success: bool = True
    graph_id: str
    graph_uri: str
    dataset: str
    message: str
