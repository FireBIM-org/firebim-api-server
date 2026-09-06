from typing import Any

from pydantic import BaseModel, Field


class RuleElementPayload(BaseModel):
    id: str
    element_type: str = "decision"

    target_class: str | None = None
    property_path: str | None = None
    operator: str | None = None
    value: Any | None = None
    expression: str | None = Field(
        default=None,
        description="derive elements only: expression written to property_path (see app.rules.expressions)",
    )

    true_path: str | None = None
    false_path: str | None = None
    unknown_path: str | None = None

    label: str | None = None
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
            "expression": self.expression,
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
    entry: str | None = None
    element_count: int
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleCompileRequest(RuleEnvelope):
    pass


class RuleCompileResponse(BaseModel):
    rule_id: str
    element_count: int
    ttl: str
    warnings: list[str] = Field(default_factory=list)
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleDecompileRequest(BaseModel):
    ttl: str


class RuleDecompileResponse(BaseModel):
    rule: dict | None = None
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RulePublishRequest(RuleEnvelope):
    pass


class RulePublishResponse(BaseModel):
    published: bool
    rule_id: str | None = None
    graph_uri: str | None = None
    entry: str | None = None
    element_count: int = 0
    label: str | None = None
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleFetchRequest(BaseModel):
    rule_id: str


class RuleFetchResponse(BaseModel):
    graph_uri: str
    ttl: str = ""
    rule: dict | None = None
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleDeleteRequest(BaseModel):
    rule_id: str


class RuleDeleteResponse(BaseModel):
    deleted: bool
    graph_uri: str
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleTermDescriptor(BaseModel):
    value: str
    full_iri: str
    namespace: str
    local_name: str


class RuleTermPairDescriptor(BaseModel):
    value: str
    full_pair: str
    compact_pair: str
    target_class: RuleTermDescriptor
    property_path: RuleTermDescriptor


class RuleTermLocalNameGroup(BaseModel):
    local_name: str
    count: int
    full_iris: list[str] = Field(default_factory=list)
    namespaces: list[str] = Field(default_factory=list)


class RuleTermStats(BaseModel):
    rule_count: int
    element_count: int
    rules_with_target_classes: int
    rules_with_property_paths: int
    rules_with_both: int
    elements_with_target_class: int
    elements_with_property_path: int
    elements_with_both: int
    unique_target_classes: int
    unique_target_class_namespaces: int
    unique_target_class_local_names: int
    unique_property_paths: int
    unique_property_path_namespaces: int
    unique_property_path_local_names: int
    unique_target_property_pairs: int
    target_class_local_name_collisions: int
    property_path_local_name_collisions: int


class RuleTermsResponse(BaseModel):
    rule_id: str
    compact_terms: bool = False
    target_classes: list[RuleTermDescriptor] = Field(default_factory=list)
    property_paths: list[RuleTermDescriptor] = Field(default_factory=list)
    target_property_pairs: list[RuleTermPairDescriptor] = Field(default_factory=list)
    target_class_local_name_groups: list[RuleTermLocalNameGroup] = Field(default_factory=list)
    property_path_local_name_groups: list[RuleTermLocalNameGroup] = Field(default_factory=list)
    stats: RuleTermStats
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleTermsAggregateRequest(BaseModel):
    rule_ids: list[str] = Field(default_factory=list)
    compact_terms: bool = False


class RuleTermsAggregateResponse(BaseModel):
    rule_ids: list[str] = Field(default_factory=list)
    compact_terms: bool = False
    target_classes: list[RuleTermDescriptor] = Field(default_factory=list)
    property_paths: list[RuleTermDescriptor] = Field(default_factory=list)
    target_property_pairs: list[RuleTermPairDescriptor] = Field(default_factory=list)
    target_class_local_name_groups: list[RuleTermLocalNameGroup] = Field(default_factory=list)
    property_path_local_name_groups: list[RuleTermLocalNameGroup] = Field(default_factory=list)
    stats: RuleTermStats
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleListItem(BaseModel):
    rule_id: str
    graph_uri: str
    entry: str | None = None
    label: str | None = None
    element_count: int = 0


class RuleListResponse(BaseModel):
    count: int
    rules: list[RuleListItem] = Field(default_factory=list)
    success: bool = True
    warning: str | None = None
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleElementPublishRequest(RuleElementPayload):
    pass


class RuleElementPublishResponse(BaseModel):
    published: bool
    element_id: str | None = None
    graph_uri: str | None = None
    element_type: str | None = None
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleElementFetchRequest(BaseModel):
    element_id: str


class RuleElementFetchResponse(BaseModel):
    graph_uri: str
    ttl: str = ""
    rule_element: dict | None = None
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleElementDeleteRequest(BaseModel):
    element_id: str


class RuleElementDeleteResponse(BaseModel):
    deleted: bool
    graph_uri: str
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleElementListItem(BaseModel):
    element_id: str
    graph_uri: str
    element_type: str | None = None
    label: str | None = None


class RuleElementListResponse(BaseModel):
    count: int
    graph_uris: list[str] = Field(default_factory=list)
    rule_elements: list[RuleElementListItem] = Field(default_factory=list)
    success: bool = True
    warning: str | None = None
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class RuleConnectRequest(BaseModel):
    rule: RuleDraft
    source_id: str
    branch: str
    target_id: str


class RuleConnectResponse(BaseModel):
    updated: bool
    overwritten: bool
    previous_target_id: str | None = None
    rule: dict


class RuleDisconnectRequest(BaseModel):
    rule: RuleDraft
    source_id: str
    branch: str


class RuleDisconnectResponse(BaseModel):
    updated: bool
    previous_target_id: str | None = None
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
    graph_uri: str | None = None


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
    error: str | None = None
    message: str | None = None
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
    error: str | None = None
    message: str | None = None
    fallback: bool = False
