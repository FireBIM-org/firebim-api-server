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