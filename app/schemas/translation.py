from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator

_NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_ProfileName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class TranslationSource(BaseModel):
    """Native IFC input; schema_ is serialized and accepted as the JSON field schema."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    schema_: Literal["IFC4"] = Field(default="IFC4", alias="schema")
    adapter: Literal["native_ifc"] = "native_ifc"
    extraction_contract: Literal["native-ifc-record/1"] = "native-ifc-record/1"


class TranslationOntologyPin(BaseModel):
    """Draft module identity and optional pin metadata; no resolution or verification is performed."""

    model_config = ConfigDict(extra="forbid")

    family: _NonBlank
    country: _NonBlank
    version: _NonBlank = "2026.09.04"
    ontology_iri: str | None = None
    version_iri: str | None = None
    sha256: str | None = None
    artifact_ref: str | None = None
    dependencies: list[str] = Field(default_factory=list)


class TranslationDraftMapping(BaseModel):
    """Opaque authoring data, including incomplete or unsupported definitions.

    Saving never implies validation. Enabled, accepted definitions are checked separately
    against the bounded IFC4 language; unsupported data can still be retained in drafts.
    """

    model_config = ConfigDict(extra="forbid")

    id: _NonBlank
    enabled: bool = False
    review_state: Literal["suggestion", "accepted", "rejected", "unresolved"] = "unresolved"
    seed_provenance: dict[str, JsonValue] | None = None
    definition: dict[str, JsonValue] = Field(default_factory=dict)


class TranslationContract(BaseModel):
    """Draft envelope; semantic validity and production execution availability are checked separately."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ifc_translation"] = "ifc_translation"
    contract_version: Literal["1"] = "1"
    source: TranslationSource = Field(default_factory=TranslationSource)
    destination_release: Literal["2026.09.04"] = "2026.09.04"
    destination_modules: list[TranslationOntologyPin] = Field(default_factory=list)
    mappings: list[TranslationDraftMapping] = Field(default_factory=list)
    optional_rule_ids: list[str] = Field(default_factory=list)


class TranslationProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ifc_translation"]
    name: _ProfileName
    description: str | None = None
    contract: TranslationContract = Field(default_factory=TranslationContract)


class TranslationProfileUpdateRequest(BaseModel):
    """Use model_fields_set to distinguish omission from explicitly clearing description."""

    model_config = ConfigDict(extra="forbid")

    expected_generation: int = Field(..., ge=1)
    name: _ProfileName | None = None
    description: str | None = None
    contract: TranslationContract | None = None

    @field_validator("name", "contract")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("Omit this field to retain its value; it cannot be null")
        return value


class TranslationRevisionReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: _NonBlank
    draft_generation: int = Field(ge=1)
    content_digest: str
    published_at: str


class TranslationStoredProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonBlank
    name: _ProfileName
    description: str | None = None
    kind: Literal["ifc_translation"] = "ifc_translation"
    status: Literal["draft", "archived"] = "draft"
    generation: int = Field(default=1, ge=1)
    contract: TranslationContract
    created_at: str
    updated_at: str
    revision_refs: list[TranslationRevisionReference] = Field(default_factory=list)


class TranslationProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonBlank
    name: _ProfileName
    description: str | None = None
    kind: Literal["ifc_translation"] = "ifc_translation"
    status: Literal["draft", "archived"] = "draft"
    generation: int = Field(default=1, ge=1)
    updated_at: str


class TranslationGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: int = Field(..., ge=1)


class TranslationValidationDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["error", "warning", "info"]
    mapping_id: str | None = None
    field_path: str
    message: str


class TranslationValidationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Literal["destination_ontologies", "mappings", "publication"]
    status: Literal["passed", "failed", "unavailable"]
    message: str


class TranslationValidationReport(BaseModel):
    """publish_ready permits retaining a validated revision, not executing it on uploads."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    generation: int
    authoring_valid: bool
    runtime_available: Literal[False] = False
    publish_ready: bool = False
    steps: list[TranslationValidationStep]
    diagnostics: list[TranslationValidationDiagnostic]


class TranslationMappingExtractionReport(BaseModel):
    """Read-only suggestions for one saved generation; save explicitly to retain them."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    generation: int
    mappings: list[TranslationDraftMapping]
    skipped_existing: int = Field(default=0, ge=0)
    diagnostics: list[TranslationValidationDiagnostic]
