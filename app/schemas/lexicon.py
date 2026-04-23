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
    issues: list[LexiconConformityIssue] = Field(default_factory=list)
