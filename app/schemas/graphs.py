from __future__ import annotations

from pydantic import BaseModel, Field


class GraphsHealthResponse(BaseModel):
    status: str
    storage_connected: bool
    dataset: str
    storage_backend: str | None = None
    storage_host: str | None = None


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
    label: str | None = None
    source: str | None = None


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


class GraphFetchResponse(BaseModel):
    success: bool = True
    graph_id: str
    graph_uri: str
    ttl: str | None = None
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)


class GraphSchemaExtractRequest(BaseModel):
    graph_id: str = Field(..., min_length=1, description="Stable graph identifier")
    include_examples: bool = False
    max_examples_per_edge: int = Field(default=3, ge=1, le=20)


class GraphSchemaEdgeSummary(BaseModel):
    from_class: str
    to_class: str
    predicates: list[str] = Field(default_factory=list)
    support: int = 0
    examples: list[dict[str, str]] = Field(default_factory=list)


class GraphSchemaExtractResponse(BaseModel):
    success: bool = True
    graph_id: str
    graph_uri: str
    triple_count: int = 0
    class_counts: dict[str, int] = Field(default_factory=dict)
    property_counts: dict[str, int] = Field(default_factory=dict)
    class_to_class_edges: list[GraphSchemaEdgeSummary] = Field(default_factory=list)
    forward_predicates_by_class_pair: dict[str, list[str]] = Field(default_factory=dict)
    reverse_predicates_by_class_pair: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
