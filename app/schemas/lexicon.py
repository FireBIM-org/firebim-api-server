from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.rules import RuleDraft


class LexiconHealthResponse(BaseModel):
    status: str
    ontologies_connected: bool


class LexiconBranchReference(BaseModel):
    branch: str
    target_node_id: str | None = None


class LexiconPropertyRequirement(BaseModel):
    property_path: str
    node_ids: list[str] = Field(default_factory=list)
    operators: list[str] = Field(default_factory=list)
    sample_values: list[str] = Field(default_factory=list)


class LexiconExecutionTarget(BaseModel):
    target_class: str
    node_ids: list[str] = Field(default_factory=list)
    property_requirements: list[LexiconPropertyRequirement] = Field(default_factory=list)
    relation_requirements: list[str] = Field(default_factory=list)


class LexiconExecutionProfileNode(BaseModel):
    node_id: str
    element_type: str | None = None
    label: str | None = None
    target_class: str | None = None
    property_path: str | None = None
    operator: str | None = None
    value: Any | None = None
    branches: list[LexiconBranchReference] = Field(default_factory=list)


class LexiconExecutionProfile(BaseModel):
    rule_id: str
    entry_node_id: str | None = None
    node_count: int
    complete: bool
    open: bool
    broken: bool
    target_classes: list[str] = Field(default_factory=list)
    property_paths: list[str] = Field(default_factory=list)
    relation_paths: list[str] = Field(default_factory=list)
    targets: list[LexiconExecutionTarget] = Field(default_factory=list)
    nodes: list[LexiconExecutionProfileNode] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LexiconProfileDeriveRequest(BaseModel):
    ontology_family: str = Field(default="fbo", min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(default="latest", min_length=2)
    rule_id: str | None = None
    rule: RuleDraft | None = None

    @model_validator(mode="after")
    def validate_rule_source(self) -> LexiconProfileDeriveRequest:
        if bool(self.rule_id) == bool(self.rule):
            raise ValueError("Provide exactly one of rule_id or rule")
        return self


class LexiconProfileDeriveResponse(BaseModel):
    ontology_family: str
    country: str
    version: str
    canonical_family_uri: str
    canonical_country_uri: str
    profile: LexiconExecutionProfile


class LexiconEntityPropertyInput(BaseModel):
    property_uri: str
    value: Any
    datatype_uri: str | None = None
    language: str | None = None


class LexiconEntityRelationInput(BaseModel):
    predicate_uri: str
    target_entity_id: str


class LexiconEntityInput(BaseModel):
    entity_id: str
    class_uri: str
    additional_class_uris: list[str] = Field(default_factory=list)
    label: str | None = None
    properties: list[LexiconEntityPropertyInput] = Field(default_factory=list)
    relations: list[LexiconEntityRelationInput] = Field(default_factory=list)


class LexiconReconstructionRequest(BaseModel):
    ontology_family: str = Field(default="fbo", min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(default="latest", min_length=2)
    graph_id: str = Field(default="lexicon-reconstruction", min_length=1)
    instance_base_uri: str | None = None
    entities: list[LexiconEntityInput] = Field(default_factory=list)


class LexiconReconstructionResponse(BaseModel):
    ontology_family: str
    country: str
    version: str
    graph_id: str
    graph_uri: str
    canonical_family_uri: str
    canonical_country_uri: str
    ontology_uri: str | None = None
    version_iri: str | None = None
    triple_count: int
    ttl: str


class LexiconConformityCheckRequest(BaseModel):
    ontology_family: str = Field(default="fbo", min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(default="latest", min_length=2)
    ttl: str = Field(..., min_length=1)


class LexiconConformityIssue(BaseModel):
    code: str
    message: str
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None


class LexiconNeededProperty(BaseModel):
    target_class: str
    property_path: str
    node_ids: list[str] = Field(default_factory=list)


class LexiconConformityCheckResponse(BaseModel):
    ontology_family: str
    country: str
    version: str
    canonical_country_uri: str
    ontology_uri: str | None = None
    version_iri: str | None = None
    triple_count: int
    structure_ok: bool
    semantic_ok: bool
    execution_ready: bool
    conforms: bool
    unknown_classes: list[str] = Field(default_factory=list)
    unknown_properties: list[str] = Field(default_factory=list)
    missing_type_subjects: list[str] = Field(default_factory=list)
    profile_alignment_ok: bool = True
    missing_profile_target_classes: list[str] = Field(default_factory=list)
    missing_profile_properties: list[LexiconNeededProperty] = Field(default_factory=list)
    issues: list[LexiconConformityIssue] = Field(default_factory=list)


class LexiconCatalogTermSummary(BaseModel):
    term_id: str
    uri: str
    kind: str
    label: str | None = None
    definition: str | None = None
    description: str | None = None
    parent_term_ids: list[str] = Field(default_factory=list)
    is_predefined_type: bool = False


class LexiconCatalogTermDetail(BaseModel):
    term_id: str
    uri: str
    kind: str
    label: str | None = None
    definition: str | None = None
    description: str | None = None
    parent_term_ids: list[str] = Field(default_factory=list)
    child_term_ids: list[str] = Field(default_factory=list)
    property_term_ids: list[str] = Field(default_factory=list)
    is_predefined_type: bool = False


class LexiconCatalogTermsResponse(BaseModel):
    ontology_family: str
    country: str
    version: str
    canonical_family_uri: str
    canonical_country_uri: str
    count: int
    total_count: int
    next_cursor: str | None = None
    terms: list[LexiconCatalogTermSummary] = Field(default_factory=list)


class LexiconCatalogTermResponse(BaseModel):
    ontology_family: str
    country: str
    version: str
    canonical_family_uri: str
    canonical_country_uri: str
    term: LexiconCatalogTermDetail


class LexiconMappedTermReference(BaseModel):
    ontology_family: str = Field(..., min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(default="latest", min_length=2)
    term_id: str = Field(..., min_length=1)
    uri: str | None = None


class LexiconSourceTermCondition(BaseModel):
    term_kind: str = Field(..., pattern="^(class|property)$")
    ontology_family: str = Field(..., min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(default="latest", min_length=2)
    term_id: str = Field(..., min_length=1)
    uri: str | None = None


class LexiconSourcePatternClause(BaseModel):
    term: LexiconSourceTermCondition | None = None
    all_of: list[LexiconSourcePatternClause] = Field(default_factory=list)
    any_of: list[LexiconSourcePatternClause] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_clause_shape(self) -> LexiconSourcePatternClause:
        active = int(self.term is not None) + int(bool(self.all_of)) + int(bool(self.any_of))
        if active != 1:
            raise ValueError("Each source pattern clause must define exactly one of term, all_of, or any_of")
        return self


class LexiconSourcePattern(BaseModel):
    all_of: list[LexiconSourcePatternClause] = Field(default_factory=list)
    any_of: list[LexiconSourcePatternClause] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_pattern_shape(self) -> LexiconSourcePattern:
        active = int(bool(self.all_of)) + int(bool(self.any_of))
        if active != 1:
            raise ValueError("Source pattern must define exactly one of all_of or any_of")
        return self


class LexiconTargetAssertion(BaseModel):
    assertion_kind: str = Field(..., pattern="^(class|property|property_value)$")
    ontology_family: str = Field(..., min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(default="latest", min_length=2)
    term_id: str = Field(..., min_length=1)
    uri: str | None = None
    value: Any | None = None
    datatype_uri: str | None = None
    language: str | None = None

    @model_validator(mode="after")
    def validate_assertion_shape(self) -> LexiconTargetAssertion:
        if self.assertion_kind == "class" and self.value is not None:
            raise ValueError("Class assertions must not define a value")
        if self.assertion_kind == "property" and self.value is not None:
            raise ValueError("Property assertions must not define a value")
        if self.assertion_kind == "property_value" and self.value is None:
            raise ValueError("Property_value assertions must define a value")
        return self


class LexiconMappingValidationRequest(BaseModel):
    mapping_kind: str = Field(..., pattern="^(class_to_class|property_to_property|semantic_assertion)$")
    source_term: LexiconMappedTermReference | None = None
    target_term: LexiconMappedTermReference | None = None
    source_pattern: LexiconSourcePattern | None = None
    target_assertions: list[LexiconTargetAssertion] = Field(default_factory=list)
    relationship: str | None = None

    @model_validator(mode="after")
    def validate_mapping_shape(self) -> LexiconMappingValidationRequest:
        uses_simple = self.source_term is not None or self.target_term is not None
        uses_pattern = self.source_pattern is not None or bool(self.target_assertions)

        if uses_simple and uses_pattern:
            raise ValueError("Provide either source_term/target_term or source_pattern/target_assertions, not both")

        if self.mapping_kind in {"class_to_class", "property_to_property"} and (
            self.source_term is None or self.target_term is None
        ):
            raise ValueError(f"{self.mapping_kind} requires both source_term and target_term")
        if self.mapping_kind == "semantic_assertion" and (self.source_pattern is None or not self.target_assertions):
            raise ValueError("semantic_assertion requires source_pattern and at least one target_assertion")

        return self


class LexiconMappingValidationResponse(BaseModel):
    valid: bool
    mapping_kind: str
    source_kind: str | None = None
    target_kind: str | None = None
    compatible: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class LexiconMappingCreateRequest(LexiconMappingValidationRequest):
    name: str | None = None
    notes: str | None = None


class LexiconMapping(LexiconMappingCreateRequest):
    id: str
    name: str
    created_at: str
    updated_at: str


class LexiconMappingSummary(BaseModel):
    id: str
    name: str
    mapping_kind: str
    relationship: str | None = None
    created_at: str
    updated_at: str
    source_term_count: int
    target_assertion_count: int


class LexiconMappingResponse(BaseModel):
    mapping: LexiconMapping


class LexiconMappingListResponse(BaseModel):
    count: int
    mappings: list[LexiconMappingSummary] = Field(default_factory=list)


class LexiconMappingDeleteResponse(BaseModel):
    deleted: bool
    mapping_id: str


class LexiconEvidenceItem(BaseModel):
    kind: str
    source_object_ref: str | None = None
    term_id: str | None = None
    property_set: str | None = None
    property_code: str | None = None
    value: Any | None = None
    notes: str | None = None


class LexiconAcceptedTarget(BaseModel):
    target_class: str
    source_object_ref: str | None = None
    mapping_ids: list[str] = Field(default_factory=list)
    property_paths: list[str] = Field(default_factory=list)
    evidence: list[LexiconEvidenceItem] = Field(default_factory=list)


class LexiconHandshakeAcceptedPayload(BaseModel):
    mapping_ids: list[str] = Field(default_factory=list)
    targets: list[LexiconAcceptedTarget] = Field(default_factory=list)


class LexiconHandshake(BaseModel):
    id: str
    ontology_family: str
    country: str
    version: str
    canonical_family_uri: str
    canonical_country_uri: str
    profile: LexiconExecutionProfile
    accepted_payload: LexiconHandshakeAcceptedPayload
    created_at: str
    updated_at: str


class LexiconHandshakeCreateRequest(BaseModel):
    ontology_family: str = Field(default="fbo", min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(default="latest", min_length=2)
    rule_id: str | None = None
    rule: RuleDraft | None = None
    accepted_payload: LexiconHandshakeAcceptedPayload = Field(default_factory=LexiconHandshakeAcceptedPayload)

    @model_validator(mode="after")
    def validate_rule_source(self) -> LexiconHandshakeCreateRequest:
        if bool(self.rule_id) == bool(self.rule):
            raise ValueError("Provide exactly one of rule_id or rule")
        return self


class LexiconHandshakeAcceptRequest(BaseModel):
    accepted_payload: LexiconHandshakeAcceptedPayload


class LexiconHandshakeResponse(BaseModel):
    handshake: LexiconHandshake


class LexiconHandshakeReconstructionRequest(BaseModel):
    graph_id: str = Field(default="lexicon-handshake-reconstruction", min_length=1)
    instance_base_uri: str | None = None


class LexiconHandshakeConformityRequest(BaseModel):
    graph_id: str = Field(default="lexicon-handshake-conformity", min_length=1)
    instance_base_uri: str | None = None


class LexiconNextNeededResponse(BaseModel):
    handshake_id: str
    complete: bool
    remaining_target_classes: list[str] = Field(default_factory=list)
    remaining_properties: list[LexiconNeededProperty] = Field(default_factory=list)


LexiconSourcePatternClause.model_rebuild()
LexiconSourcePattern.model_rebuild()
