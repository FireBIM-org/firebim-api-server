from typing import Any, Optional

from pydantic import BaseModel, Field


class RuleElementPayload(BaseModel):
    id: str
    element_type: str = "decision"

    target_class: Optional[str] = None
    property_path: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Any] = None

    true_path: Optional[str] = None
    false_path: Optional[str] = None
    unknown_path: Optional[str] = None

    label: Optional[str] = None
    config: dict = Field(default_factory=dict)

    def as_internal_dict(self) -> dict:
        return {
            "id": self.id,
            "element_type": self.element_type,
            "node_type": self.element_type,
            "target_class": self.target_class,
            "property_path": self.property_path,
            "operator": self.operator,
            "value": self.value,
            "true_path": self.true_path,
            "false_path": self.false_path,
            "unknown_path": self.unknown_path,
            "label": self.label,
            "config": self.config,
        }


class RuleDraft(BaseModel):
    id: str
    entry: str
    elements: list[RuleElementPayload] = Field(default_factory=list)

    def as_internal_dict(self) -> dict:
        elements = [element.as_internal_dict() for element in self.elements]
        return {
            "id": self.id,
            "entry": self.entry,
            "elements": elements,
        }


class RuleEnvelope(BaseModel):
    rule: RuleDraft


class RuleValidateRequest(RuleEnvelope):
    pass


class RuleValidateResponse(BaseModel):
    valid: bool
    rule_id: str
    entry: Optional[str] = None
    element_count: int
    errors: list[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleCompileRequest(RuleEnvelope):
    pass


class RuleCompileResponse(BaseModel):
    rule_id: str
    element_count: int
    ttl: str
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleDecompileRequest(BaseModel):
    ttl: str


class RuleDecompileResponse(BaseModel):
    rule: dict | None = None
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RulePublishRequest(RuleEnvelope):
    pass


class RulePublishResponse(BaseModel):
    published: bool
    rule_id: Optional[str] = None
    graph_uri: Optional[str] = None
    entry: Optional[str] = None
    element_count: int = 0
    label: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleFetchRequest(BaseModel):
    rule_id: str


class RuleFetchResponse(BaseModel):
    graph_uri: str
    ttl: str = ""
    rule: dict | None = None
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleDeleteRequest(BaseModel):
    rule_id: str


class RuleDeleteResponse(BaseModel):
    deleted: bool
    graph_uri: str
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleListItem(BaseModel):
    rule_id: str
    graph_uri: str
    entry: Optional[str] = None
    label: Optional[str] = None
    element_count: int = 0


class RuleListResponse(BaseModel):
    count: int
    rules: list[RuleListItem] = Field(default_factory=list)
    success: bool = True
    warning: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleElementPublishRequest(RuleElementPayload):
    pass


class RuleElementPublishResponse(BaseModel):
    published: bool
    element_id: Optional[str] = None
    graph_uri: Optional[str] = None
    element_type: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleElementFetchRequest(BaseModel):
    element_id: str


class RuleElementFetchResponse(BaseModel):
    graph_uri: str
    ttl: str = ""
    rule_element: dict | None = None
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleElementDeleteRequest(BaseModel):
    element_id: str


class RuleElementDeleteResponse(BaseModel):
    deleted: bool
    graph_uri: str
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleElementListItem(BaseModel):
    element_id: str
    graph_uri: str
    element_type: Optional[str] = None
    label: Optional[str] = None


class RuleElementListResponse(BaseModel):
    count: int
    graph_uris: list[str] = Field(default_factory=list)
    rule_elements: list[RuleElementListItem] = Field(default_factory=list)
    success: bool = True
    warning: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleConnectRequest(BaseModel):
    rule: RuleDraft
    source_id: str
    branch: str
    target_id: str


class RuleConnectResponse(BaseModel):
    updated: bool
    overwritten: bool
    previous_target_id: Optional[str] = None
    rule: dict


class RuleDisconnectRequest(BaseModel):
    rule: RuleDraft
    source_id: str
    branch: str


class RuleDisconnectResponse(BaseModel):
    updated: bool
    previous_target_id: Optional[str] = None
    rule: dict


class RuleCompletenessRequest(BaseModel):
    rule: RuleDraft


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
    rule: RuleDraft
    mode: str = "per_branch"
    node_type: str = "end"
    label_prefix: str = "forced_end"


class RuleForceEndResponse(BaseModel):
    updated: bool
    filled_count: int
    created_node_ids: list[str]
    rule: dict


class RuleElementJson(RuleElementPayload):
    graph_uri: Optional[str] = None


class RuleElementJsonResponse(BaseModel):
    rule_element: RuleElementJson


class RuleElementDeriveRequest(BaseModel):
    source_id: str
    new_id: str
    preserve_paths: bool = False
    overrides: dict = Field(default_factory=dict)


class RuleElementDeriveResponse(BaseModel):
    rule_element: RuleElementJson


class RuleLinkRequest(BaseModel):
    source_id: str
    branch: str
    target_id: str


class RuleAssembleRequest(BaseModel):
    rule_id: str
    entry: str
    element_ids: list[str] = Field(default_factory=list)
    links: list[RuleLinkRequest] = Field(default_factory=list)
    validate: bool = True


class RuleAssembleResponse(BaseModel):
    rule: dict | None = None
    element_count: int = 0
    complete: bool = False
    open: bool = False
    broken: bool = False
    entry_ok: bool = False
    missing_entry: bool = False
    missing_nodes: list[str] = Field(default_factory=list)
    open_branches: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False


class RuleAssembleCompileResponse(BaseModel):
    rule: dict | None = None
    rule_id: str
    element_count: int = 0
    ttl: str = ""
    complete: bool = False
    open: bool = False
    broken: bool = False
    errors: list[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    message: Optional[str] = None
    fallback: bool = False
