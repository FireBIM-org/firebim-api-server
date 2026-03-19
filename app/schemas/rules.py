from typing import Any, List, Optional
from pydantic import BaseModel, Field


class RuleNodePayload(BaseModel):
    id: str
    node_type: str = "decision"

    target_class: Optional[str] = None
    property_path: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Any] = None

    true_path: Optional[str] = None
    false_path: Optional[str] = None
    unknown_path: Optional[str] = None

    label: Optional[str] = None
    config: dict = Field(default_factory=dict)

class RuleSetValidateRequest(BaseModel):
    id: str
    entry: str
    nodes: List[RuleNodePayload]

class RuleSetValidateResponse(BaseModel):
    valid: bool
    ruleset_id: str
    entry: Optional[str] = None
    node_count: int
    errors: List[str] = Field(default_factory=list)

class RuleSetCompileResponse(BaseModel):
    ruleset_id: str
    node_count: int
    ttl: str

class RuleSetDecompileRequest(BaseModel):
    ttl: str

class RuleSetDecompileResponse(BaseModel):
    ruleset: dict

class RuleSetPublishRequest(BaseModel):
    id: str
    entry: str
    nodes: List[RuleNodePayload]
    graph_uri: Optional[str] = None

class RuleSetPublishResponse(BaseModel):
    published: bool
    ruleset_id: str
    graph_uri: str
    node_count: int

class RuleSetFetchRequest(BaseModel):
    graph_uri: str

class RuleSetFetchResponse(BaseModel):
    graph_uri: str
    ttl: str
    ruleset: dict | None = None

class RuleSetDeleteRequest(BaseModel):
    graph_uri: str

class RuleSetDeleteResponse(BaseModel):
    deleted: bool
    graph_uri: str

class RuleElementPublishRequest(BaseModel):
    id: str
    node_type: str = "decision"

    target_class: Optional[str] = None
    property_path: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Any] = None

    true_path: Optional[str] = None
    false_path: Optional[str] = None
    unknown_path: Optional[str] = None

    label: Optional[str] = None
    config: dict = Field(default_factory=dict)

    graph_uri: Optional[str] = None

class RuleElementPublishResponse(BaseModel):
    published: bool
    element_id: str
    graph_uri: str
    node_type: str

class RuleElementFetchRequest(BaseModel):
    graph_uri: str

class RuleElementFetchResponse(BaseModel):
    graph_uri: str
    ttl: str
    rule_element: dict | None = None

class RuleElementDeleteRequest(BaseModel):
    graph_uri: str

class RuleElementDeleteResponse(BaseModel):
    deleted: bool
    graph_uri: str

class RuleElementListResponse(BaseModel):
    count: int
    graph_uris: list[str]

class RuleSetDraft(BaseModel):
    id: str
    entry: str
    nodes: List[RuleNodePayload]

class RuleConnectRequest(BaseModel):
    ruleset: RuleSetDraft
    source_id: str
    branch: str
    target_id: str

class RuleConnectResponse(BaseModel):
    updated: bool
    overwritten: bool
    previous_target_id: Optional[str] = None
    ruleset: dict

class RuleDisconnectRequest(BaseModel):
    ruleset: RuleSetDraft
    source_id: str
    branch: str

class RuleDisconnectResponse(BaseModel):
    updated: bool
    previous_target_id: Optional[str] = None
    ruleset: dict

class RuleCompletenessRequest(BaseModel):
    ruleset: RuleSetDraft

class RuleCompletenessResponse(BaseModel):
    complete: bool
    open: bool
    broken: bool
    entry_ok: bool
    missing_entry: bool
    missing_nodes: list[str]
    open_branches: list[dict]
    errors: list[str]

class RuleForceEndRequest(BaseModel):
    ruleset: RuleSetDraft
    mode: str = "per_branch"   # Accepts: shared | per_branch | per_open_path
    node_type: str = "end"
    label_prefix: str = "forced_end"

class RuleForceEndResponse(BaseModel):
    updated: bool
    filled_count: int
    created_node_ids: list[str]
    ruleset: dict