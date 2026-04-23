from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field

from app.schemas.rules import RuleElementPayload


class RuleBuilderCanvasPosition(BaseModel):
    x: float
    y: float


class RuleBuilderViewport(BaseModel):
    x: float
    y: float
    zoom: float = Field(..., gt=0)


class RuleBuilderCanvasNode(BaseModel):
    canvas_node_id: str = Field(..., min_length=1)
    position: RuleBuilderCanvasPosition
    rule: RuleElementPayload


class RuleBuilderCanvasEdge(BaseModel):
    id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("id", "edge_id"),
        serialization_alias="id",
    )
    source: str = Field(..., min_length=1)
    source_handle: str | None = None
    target: str = Field(..., min_length=1)
    target_handle: str | None = None


class RuleBuilderWorkspacePayload(BaseModel):
    name: str | None = None
    entry_node_id: str = Field(..., min_length=1)
    entry_node_label: str | None = None
    viewport: RuleBuilderViewport
    nodes: list[RuleBuilderCanvasNode] = Field(default_factory=list)
    edges: list[RuleBuilderCanvasEdge] = Field(default_factory=list)


class RuleBuilderWorkspaceCreateRequest(RuleBuilderWorkspacePayload):
    pass


class RuleBuilderWorkspaceUpdateRequest(RuleBuilderWorkspacePayload):
    pass


class RuleBuilderWorkspace(BaseModel):
    id: str
    name: str
    entry_node_id: str
    entry_node_label: str | None = None
    viewport: RuleBuilderViewport
    nodes: list[RuleBuilderCanvasNode] = Field(default_factory=list)
    edges: list[RuleBuilderCanvasEdge] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RuleBuilderWorkspaceSummary(BaseModel):
    id: str
    name: str
    entry_node_id: str
    entry_node_label: str | None = None
    created_at: datetime
    updated_at: datetime
    node_count: int = 0
    edge_count: int = 0


class RuleBuilderWorkspaceResponse(BaseModel):
    workspace: RuleBuilderWorkspace


class RuleBuilderWorkspaceListResponse(BaseModel):
    count: int
    workspaces: list[RuleBuilderWorkspaceSummary] = Field(default_factory=list)


class RuleBuilderWorkspaceDeleteResponse(BaseModel):
    deleted: bool
    workspace_id: str


class RuleBuilderWorkspaceBatchDeleteRequest(BaseModel):
    workspace_ids: list[str] = Field(default_factory=list)


class RuleBuilderWorkspaceBatchDeleteResponse(BaseModel):
    deleted_ids: list[str] = Field(default_factory=list)
    not_found_ids: list[str] = Field(default_factory=list)


class RuleBuilderBulkSavedElement(BaseModel):
    source_id: str
    element_id: str
    graph_uri: str


class RuleBuilderExportRuleElementsResponse(BaseModel):
    saved: bool = True
    workspace_id: str
    group_id: str
    group_name: str
    entry_node_id: str
    entry_node_label: str | None = None
    created_count: int = 0
    elements: list[RuleBuilderBulkSavedElement] = Field(default_factory=list)
    duplicate_rule_element_ids: list[str] = Field(default_factory=list)
