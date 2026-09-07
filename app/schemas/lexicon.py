from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.rules import RuleDraft


class LexiconHealthResponse(BaseModel):
    status: str
    ontologies_connected: bool


class LexiconSelectedOntology(BaseModel):
    ontology_family: str = Field(..., min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(default="latest", min_length=2)
    canonical_family_uri: str | None = None
    canonical_country_uri: str | None = None


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
    rule_ids: list[str] = Field(default_factory=list)
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
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    canonical_target_ontology: LexiconSelectedOntology | None = None
    rule_id: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    rule: RuleDraft | None = None
    rules: list[RuleDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rule_source(self) -> LexiconProfileDeriveRequest:
        active = int(bool(self.rule_id)) + int(bool(self.rule)) + int(bool(self.rule_ids)) + int(bool(self.rules))
        if active == 0 and getattr(self, "profile", None) is not None:
            return self
        if active != 1:
            raise ValueError("Provide exactly one of rule_id, rule_ids, rule, or rules")
        return self


class LexiconProfileDeriveResponse(BaseModel):
    ontology_family: str
    country: str
    version: str
    canonical_family_uri: str
    canonical_country_uri: str
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    canonical_target_ontology: LexiconSelectedOntology | None = None
    rule_ids: list[str] = Field(default_factory=list)
    rule_profiles: list[LexiconExecutionProfile] = Field(default_factory=list)
    profile: LexiconExecutionProfile


class LexiconProfileSummaryResponse(BaseModel):
    rule_id: str
    rule_ids: list[str] = Field(default_factory=list)
    ontology_family: str
    country: str
    version: str
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    canonical_target_ontology: LexiconSelectedOntology | None = None
    entry_node_id: str | None = None
    node_count: int
    target_count: int
    property_requirement_count: int
    complete: bool
    open: bool
    broken: bool
    publish_ready: bool
    target_classes: list[str] = Field(default_factory=list)
    property_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LexiconStoredProfile(BaseModel):
    id: str
    name: str
    description: str | None = None
    ontology_family: str
    country: str
    version: str
    canonical_family_uri: str
    canonical_country_uri: str
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    canonical_target_ontology: LexiconSelectedOntology | None = None
    rule_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    profile: LexiconExecutionProfile
    rule_profiles: list[LexiconExecutionProfile] = Field(default_factory=list)
    created_at: str
    updated_at: str


class LexiconProfileCreateRequest(LexiconProfileDeriveRequest):
    name: str | None = None
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    profile: LexiconExecutionProfile | None = None
    rule_profiles: list[LexiconExecutionProfile] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_profile_create_shape(self) -> LexiconProfileCreateRequest:
        derives = int(bool(self.rule_id)) + int(bool(self.rule_ids)) + int(bool(self.rule)) + int(bool(self.rules))
        has_explicit_profile = self.profile is not None
        if derives and has_explicit_profile:
            raise ValueError("Provide either derive inputs or an explicit profile, not both")
        if not derives and not has_explicit_profile:
            raise ValueError("Provide derive inputs or an explicit profile")
        if has_explicit_profile and not self.rule_ids:
            raise ValueError("Explicit profiles require rule_ids")
        return self


class LexiconProfileUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    keywords: list[str] | None = None
    selected_ontologies: list[LexiconSelectedOntology] | None = None
    canonical_target_ontology: LexiconSelectedOntology | None = None
    rule_ids: list[str] | None = None
    profile: LexiconExecutionProfile | None = None
    rule_profiles: list[LexiconExecutionProfile] | None = None


class LexiconProfileResponse(BaseModel):
    profile_record: LexiconStoredProfile


class LexiconProfileSummaryItem(BaseModel):
    id: str
    name: str
    description: str | None = None
    ontology_family: str
    country: str
    version: str
    rule_ids: list[str] = Field(default_factory=list)
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    target_count: int
    property_requirement_count: int
    updated_at: str


class LexiconProfileListResponse(BaseModel):
    count: int
    profiles: list[LexiconProfileSummaryItem] = Field(default_factory=list)


class LexiconProfileDeleteResponse(BaseModel):
    deleted: bool
    profile_id: str


class LexiconProfileRulesCoverageRequest(BaseModel):
    rule_id: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    rule: RuleDraft | None = None
    rules: list[RuleDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rule_source(self) -> LexiconProfileRulesCoverageRequest:
        active = int(bool(self.rule_id)) + int(bool(self.rule_ids)) + int(bool(self.rule)) + int(bool(self.rules))
        if active not in {0, 1}:
            raise ValueError("Provide at most one of rule_id, rule_ids, rule, or rules")
        return self


class LexiconRuleCoverageItem(BaseModel):
    rule_id: str
    supported: bool
    complete: bool
    missing_target_classes: list[str] = Field(default_factory=list)
    missing_properties: list[LexiconNeededProperty] = Field(default_factory=list)


class LexiconProfileRulesCoverageResponse(BaseModel):
    profile_id: str
    checked_rule_ids: list[str] = Field(default_factory=list)
    fully_supported_rule_ids: list[str] = Field(default_factory=list)
    partially_supported_rules: list[LexiconRuleCoverageItem] = Field(default_factory=list)
    unsupported_rule_ids: list[str] = Field(default_factory=list)


class LexiconProfileGraphCoverageRequest(BaseModel):
    graph_id: str = Field(..., min_length=1)


class LexiconProfileGraphAssociation(BaseModel):
    source_object_ref: str
    matched_target_classes: list[str] = Field(default_factory=list)
    matched_properties: list[str] = Field(default_factory=list)


class LexiconProfileGraphCoverageResponse(BaseModel):
    profile_id: str
    graph_id: str
    graph_uri: str
    complete: bool
    covered_target_classes: list[str] = Field(default_factory=list)
    missing_target_classes: list[str] = Field(default_factory=list)
    covered_properties: list[LexiconNeededProperty] = Field(default_factory=list)
    missing_properties: list[LexiconNeededProperty] = Field(default_factory=list)
    associations: list[LexiconProfileGraphAssociation] = Field(default_factory=list)


class LexiconProfileMappingCandidate(BaseModel):
    mapping_id: str
    name: str
    mapping_kind: str
    relevance: str
    matched_terms: list[str] = Field(default_factory=list)


class LexiconProfileMappingCandidatesResponse(BaseModel):
    profile_id: str
    count: int
    mappings: list[LexiconProfileMappingCandidate] = Field(default_factory=list)


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
    property_set_term_ids: list[str] = Field(default_factory=list)
    belongs_to_class_term_ids: list[str] = Field(default_factory=list)
    related_property_term_ids: list[str] = Field(default_factory=list)
    unit: str | None = None
    is_property_set: bool = False
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


class LexiconCatalogRelatedTermsResponse(BaseModel):
    ontology_family: str
    country: str
    version: str
    canonical_family_uri: str
    canonical_country_uri: str
    count: int
    terms: list[LexiconCatalogTermSummary] = Field(default_factory=list)


class LexiconCatalogResolveRequest(BaseModel):
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list, min_length=1)
    limit_per_term: int = Field(default=10, ge=1, le=25)


class LexiconCatalogResolvedMatch(BaseModel):
    input_term: str
    ontology_family: str
    country: str
    version: str
    canonical_country_uri: str
    match_type: str
    score: int
    term: LexiconCatalogTermSummary


class LexiconCatalogResolvedTerm(BaseModel):
    input_term: str
    count: int
    matches: list[LexiconCatalogResolvedMatch] = Field(default_factory=list)


class LexiconCatalogResolveResponse(BaseModel):
    count: int
    results: list[LexiconCatalogResolvedTerm] = Field(default_factory=list)


class LexiconCatalogCompareRequest(BaseModel):
    left: LexiconMappedTermReference
    right: LexiconMappedTermReference


class LexiconCatalogCompareResponse(BaseModel):
    left_term: LexiconCatalogTermSummary
    right_term: LexiconCatalogTermSummary
    left_kind: str
    right_kind: str
    same_kind: bool
    compatible_for_mapping: bool
    left_is_property_set: bool = False
    right_is_property_set: bool = False
    left_parent_term_ids: list[str] = Field(default_factory=list)
    right_parent_term_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LexiconSimilarityComponentScore(BaseModel):
    name: str
    score: float = Field(ge=0.0, le=1.0)
    note: str | None = None


class LexiconCatalogSimilarityRequest(BaseModel):
    left: LexiconMappedTermReference
    right: LexiconMappedTermReference


class LexiconCatalogSimilarityResponse(BaseModel):
    left_term: LexiconCatalogTermSummary
    right_term: LexiconCatalogTermSummary
    score: float = Field(ge=0.0, le=1.0)
    components: list[LexiconSimilarityComponentScore] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LexiconCatalogSimilarityMatrixRequest(BaseModel):
    left_terms: list[LexiconMappedTermReference] = Field(default_factory=list, min_length=1)
    right_terms: list[LexiconMappedTermReference] = Field(default_factory=list, min_length=1)


class LexiconCatalogSimilarityMatrixCell(BaseModel):
    left_term_id: str
    right_term_id: str
    score: float = Field(ge=0.0, le=1.0)


class LexiconCatalogSimilarityMatrixResponse(BaseModel):
    left_count: int
    right_count: int
    cells: list[LexiconCatalogSimilarityMatrixCell] = Field(default_factory=list)


class LexiconRuleOntologySimilarityRequest(BaseModel):
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list, min_length=1)
    rule_id: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    rule: RuleDraft | None = None
    rules: list[RuleDraft] = Field(default_factory=list)
    limit_per_term: int = Field(default=3, ge=1, le=25)
    compact_terms: bool = False

    @model_validator(mode="after")
    def validate_rule_source(self) -> LexiconRuleOntologySimilarityRequest:
        active = int(bool(self.rule_id)) + int(bool(self.rule_ids)) + int(bool(self.rule)) + int(bool(self.rules))
        if active != 1:
            raise ValueError("Provide exactly one of rule_id, rule_ids, rule, or rules")
        return self


class LexiconRuleSimilaritySourceTerm(BaseModel):
    term_kind: str
    value: str
    full_iri: str
    namespace: str
    local_name: str
    node_ids: list[str] = Field(default_factory=list)


class LexiconRuleSimilarityMatch(BaseModel):
    ontology_family: str
    country: str
    version: str
    term_id: str
    uri: str
    kind: str
    label: str | None = None
    value: str
    full_iri: str
    namespace: str
    local_name: str
    score: float = Field(ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class LexiconRuleSimilarityTermResult(BaseModel):
    source_term: LexiconRuleSimilaritySourceTerm
    match_count: int
    matches: list[LexiconRuleSimilarityMatch] = Field(default_factory=list)


class LexiconRuleOntologySimilarityRuleResult(BaseModel):
    rule_id: str
    target_classes: list[LexiconRuleSimilarityTermResult] = Field(default_factory=list)
    property_paths: list[LexiconRuleSimilarityTermResult] = Field(default_factory=list)


class LexiconRuleOntologySimilaritySummary(BaseModel):
    rule_id: str
    ontology_family: str
    country: str
    version: str
    source_term_count: int
    matched_source_terms: int
    matched_target_classes: int
    matched_property_paths: int
    best_score: float = Field(ge=0.0, le=1.0)
    average_top_score: float = Field(ge=0.0, le=1.0)


class LexiconRuleOntologySimilarityStats(BaseModel):
    rule_count: int
    ontology_count: int
    total_target_classes: int
    total_property_paths: int
    total_source_terms: int
    total_matches: int
    rule_ontology_pairs: int


class LexiconRuleOntologySimilarityResponse(BaseModel):
    rule_ids: list[str] = Field(default_factory=list)
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    limit_per_term: int
    compact_terms: bool = False
    results: list[LexiconRuleOntologySimilarityRuleResult] = Field(default_factory=list)
    rule_ontology_summaries: list[LexiconRuleOntologySimilaritySummary] = Field(default_factory=list)
    stats: LexiconRuleOntologySimilarityStats


class LexiconRuleTermValidationRequest(BaseModel):
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list, min_length=1)
    rule_id: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    rule: RuleDraft | None = None
    rules: list[RuleDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rule_source(self) -> LexiconRuleTermValidationRequest:
        active = int(bool(self.rule_id)) + int(bool(self.rule_ids)) + int(bool(self.rule)) + int(bool(self.rules))
        if active != 1:
            raise ValueError("Provide exactly one of rule_id, rule_ids, rule, or rules")
        return self


class LexiconRuleTermValidationMatch(BaseModel):
    ontology_family: str
    country: str
    version: str
    term_id: str
    uri: str


class LexiconRuleTermValidationIssue(BaseModel):
    rule_id: str
    node_id: str
    field: Literal["target_class", "property_path", "value", "expression"]
    expected_kind: Literal["class", "property"]
    value: str = Field(description="The term that was checked (one step of a path, or the whole field)")
    code: Literal["unknown_term", "wrong_term_kind"]
    message: str
    context: str | None = Field(default=None, description="The whole field value when `value` is one step of it")


class LexiconRuleTermValidationFieldResult(BaseModel):
    field: Literal["target_class", "property_path", "value", "expression"]
    expected_kind: Literal["class", "property"]
    value: str
    valid: bool
    matches: list[LexiconRuleTermValidationMatch] = Field(default_factory=list)
    issue: LexiconRuleTermValidationIssue | None = None


class LexiconRuleTermValidationNodeResult(BaseModel):
    rule_id: str
    node_id: str
    label: str | None = None
    valid: bool
    fields: list[LexiconRuleTermValidationFieldResult] = Field(default_factory=list)


class LexiconRuleTermValidationStats(BaseModel):
    rule_count: int
    ontology_count: int
    validated_node_count: int
    validated_field_count: int
    invalid_node_count: int
    issue_count: int


class LexiconRuleTermValidationResponse(BaseModel):
    valid: bool
    rule_ids: list[str] = Field(default_factory=list)
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    nodes: list[LexiconRuleTermValidationNodeResult] = Field(default_factory=list)
    issues: list[LexiconRuleTermValidationIssue] = Field(default_factory=list)
    stats: LexiconRuleTermValidationStats


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


class LexiconSourceEntityPattern(BaseModel):
    all_of: list[LexiconSourceTermCondition] = Field(default_factory=list)
    any_of: list[LexiconSourceTermCondition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_entity_pattern_shape(self) -> LexiconSourceEntityPattern:
        active = int(bool(self.all_of)) + int(bool(self.any_of))
        if active != 1:
            raise ValueError("Entity pattern must define exactly one of all_of or any_of")
        return self


class LexiconSourcePatternClause(BaseModel):
    term: LexiconSourceTermCondition | None = None
    entity: LexiconSourceEntityPattern | None = None
    all_of: list[LexiconSourcePatternClause] = Field(default_factory=list)
    any_of: list[LexiconSourcePatternClause] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_clause_shape(self) -> LexiconSourcePatternClause:
        active = (
            int(self.term is not None) + int(self.entity is not None) + int(bool(self.all_of)) + int(bool(self.any_of))
        )
        if active != 1:
            raise ValueError("Each source pattern clause must define exactly one of term, entity, all_of, or any_of")
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


class LexiconGraphEvidenceSummaryRequest(BaseModel):
    graph_id: str = Field(..., min_length=1)
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list, min_length=1)


class LexiconGraphEvidenceSubjectSummary(BaseModel):
    source_object_ref: str
    class_term_ids: list[str] = Field(default_factory=list)
    property_term_ids: list[str] = Field(default_factory=list)
    property_set_term_ids: list[str] = Field(default_factory=list)


class LexiconGraphEvidenceSummaryResponse(BaseModel):
    graph_id: str
    graph_uri: str
    triple_count: int
    object_count: int
    class_term_ids: list[str] = Field(default_factory=list)
    property_term_ids: list[str] = Field(default_factory=list)
    property_set_term_ids: list[str] = Field(default_factory=list)
    subjects: list[LexiconGraphEvidenceSubjectSummary] = Field(default_factory=list)


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
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    canonical_target_ontology: LexiconSelectedOntology | None = None
    profile: LexiconExecutionProfile
    accepted_payload: LexiconHandshakeAcceptedPayload
    created_at: str
    updated_at: str


class LexiconHandshakeCreateRequest(BaseModel):
    ontology_family: str = Field(default="fbo", min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(default="latest", min_length=2)
    selected_ontologies: list[LexiconSelectedOntology] = Field(default_factory=list)
    canonical_target_ontology: LexiconSelectedOntology | None = None
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


class LexiconMappingEvaluationClauseResult(BaseModel):
    clause_type: str
    matched: bool
    matched_source_objects: list[str] = Field(default_factory=list)
    missing_conditions: list[str] = Field(default_factory=list)
    children: list[LexiconMappingEvaluationClauseResult] = Field(default_factory=list)


class LexiconMappingEvaluateRequest(BaseModel):
    graph_id: str = Field(..., min_length=1)
    mapping_id: str | None = None
    mapping: LexiconMappingValidationRequest | None = None

    @model_validator(mode="after")
    def validate_source(self) -> LexiconMappingEvaluateRequest:
        if bool(self.mapping_id) == bool(self.mapping):
            raise ValueError("Provide exactly one of mapping_id or mapping")
        return self


class LexiconMappingEvaluateResponse(BaseModel):
    graph_id: str
    graph_uri: str
    mapping_kind: str
    matched: bool
    matched_source_objects: list[str] = Field(default_factory=list)
    missing_conditions: list[str] = Field(default_factory=list)
    clause_evaluation: LexiconMappingEvaluationClauseResult | None = None
    warnings: list[str] = Field(default_factory=list)


LexiconSourcePatternClause.model_rebuild()
LexiconSourcePattern.model_rebuild()
LexiconMappingEvaluationClauseResult.model_rebuild()
