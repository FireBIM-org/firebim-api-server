from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class OntologyReleaseStatus(StrEnum):
    draft = "draft"
    candidate = "candidate"
    stable = "stable"
    deprecated = "deprecated"
    withdrawn = "withdrawn"


class OntologyHealthResponse(BaseModel):
    status: str
    storage_connected: bool
    dataset: str
    storage_backend: str | None = None
    storage_host: str | None = None
    resource_directories: list[str] = Field(default_factory=list)


class OntologyPublishRequest(BaseModel):
    ontology_family: str = Field(..., min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(
        ...,
        min_length=7,
        max_length=10,
        description="Release id in YYYY.MM or YYYY.MM.DD form",
    )
    status: OntologyReleaseStatus
    issued_at: date
    ttl: str | None = Field(default=None, min_length=1, description="Ontology Turtle payload")
    resource_filename: str | None = Field(
        default=None,
        min_length=1,
        description="Optional local resource filename, for example fbo-dk.ttl",
    )
    default_version: bool = False

    @field_validator("ontology_family", "country")
    @classmethod
    def _normalize_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) not in {2, 3} or len(parts[0]) != 4 or len(parts[1]) != 2:
            raise ValueError("version must use YYYY.MM or YYYY.MM.DD format")
        year, month = parts[0], parts[1]
        if not (year.isdigit() and month.isdigit()):
            raise ValueError("version must use YYYY.MM or YYYY.MM.DD format")
        month_number = int(month)
        if month_number < 1 or month_number > 12:
            raise ValueError("version month must be between 01 and 12")
        if len(parts) == 3:
            day = parts[2]
            if len(day) != 2 or not day.isdigit():
                raise ValueError("version day must use DD in YYYY.MM.DD format")
            day_number = int(day)
            if day_number < 1 or day_number > 31:
                raise ValueError("version day must be between 01 and 31")
        return value

    @field_validator("resource_filename")
    @classmethod
    def _validate_resource_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        filename = value.strip()
        if "/" in filename or "\\" in filename:
            raise ValueError("resource_filename must be a plain filename, not a path")
        return filename

    @field_validator("ttl")
    @classmethod
    def _normalize_ttl(cls, value: str | None) -> str | None:
        # Keep the payload byte-exact so the stored sha256 matches the published file
        # (e.g. the firebim.org registry); only reject whitespace-only content.
        if value is None or not value.strip():
            return None
        return value

    @field_validator("default_version")
    @classmethod
    def _noop_default_version(cls, value: bool) -> bool:
        return bool(value)


class OntologyPublishResponse(BaseModel):
    success: bool = True
    published: bool
    ontology_family: str
    country: str
    version: str
    status: OntologyReleaseStatus
    graph_uri: str
    ontology_uri: str | None = None
    canonical_family_uri: str
    canonical_country_uri: str
    default_version: str
    overwritten: bool
    triple_count: int
    byte_size: int
    resource_filename: str | None = None
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class OntologyDictionaryConversionRequest(BaseModel):
    ontology_family: str = Field(..., min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(
        ...,
        min_length=7,
        max_length=10,
        description="Release id in YYYY.MM or YYYY.MM.DD form",
    )
    dictionary_json: dict[str, Any] | None = Field(
        default=None,
        description="Inline bSDD or dictionary JSON payload to convert into ontology TTL",
    )
    source_resource_filename: str | None = Field(
        default=None,
        min_length=1,
        description="Optional local JSON resource filename, for example rir-1.0.json or ifc-4.3.json",
    )
    output_resource_filename: str | None = Field(
        default=None,
        min_length=1,
        description="Optional local TTL resource filename to save after conversion",
    )
    publish: bool = False
    status: OntologyReleaseStatus = OntologyReleaseStatus.draft
    issued_at: date | None = None
    default_version: bool = False

    @field_validator("ontology_family", "country")
    @classmethod
    def _normalize_conversion_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("version")
    @classmethod
    def _validate_conversion_version(cls, value: str) -> str:
        return OntologyPublishRequest._validate_version(value)

    @field_validator("source_resource_filename", "output_resource_filename")
    @classmethod
    def _validate_plain_filename(cls, value: str | None) -> str | None:
        return OntologyPublishRequest._validate_resource_filename(value)

    @field_validator("dictionary_json")
    @classmethod
    def _normalize_dictionary_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return value


class OntologyDictionaryConversionResponse(BaseModel):
    success: bool = True
    ontology_family: str
    country: str
    version: str
    source_dictionary_name: str | None = None
    source_dictionary_code: str | None = None
    source_dictionary_version: str | None = None
    source_resource_filename: str | None = None
    output_resource_filename: str | None = None
    ontology_uri: str
    version_iri: str
    class_count: int
    property_count: int
    datatype_property_count: int
    object_property_count: int
    subclass_count: int = 0
    class_property_link_count: int = 0
    ttl: str
    validation_report: OntologyDictionaryValidationReport | None = None
    published: bool = False
    publish_result: OntologyPublishResponse | None = None


class OntologyDictionaryFieldIssue(BaseModel):
    item_code: str
    missing_fields: list[str] = Field(default_factory=list)


class OntologyDictionaryLinkIssue(BaseModel):
    class_code: str
    class_property_code: str | None = None
    owned_uri: str | None = None
    reason: str
    candidate_property_codes: list[str] = Field(default_factory=list)


class OntologyDictionaryValidationReport(BaseModel):
    valid: bool
    shape_ok: bool
    completeness_ok: bool
    ttl_form_ok: bool
    source_dictionary_name: str | None = None
    source_dictionary_code: str | None = None
    source_dictionary_version: str | None = None
    class_count: int
    subclass_count: int
    property_count: int
    datatype_property_count: int
    object_property_count: int
    class_property_link_count: int
    resolved_links_by_owned_uri: int
    resolved_links_by_code: int
    resolved_links_by_name: int
    unresolved_link_count: int
    ambiguous_link_count: int
    classes_missing_required_fields: list[OntologyDictionaryFieldIssue] = Field(default_factory=list)
    properties_missing_required_fields: list[OntologyDictionaryFieldIssue] = Field(default_factory=list)
    unresolved_class_property_links: list[OntologyDictionaryLinkIssue] = Field(default_factory=list)
    ambiguous_class_property_links: list[OntologyDictionaryLinkIssue] = Field(default_factory=list)
    ttl_has_ontology: bool
    ttl_has_classes: bool
    ttl_has_properties: bool
    ttl_has_class_property_links: bool
    issues: list[str] = Field(default_factory=list)


class OntologyDictionaryCandidateCreateRequest(BaseModel):
    ontology_family: str = Field(..., min_length=1)
    country: str = Field(..., min_length=2)
    version: str = Field(
        ...,
        min_length=7,
        max_length=10,
        description="Release id in YYYY.MM or YYYY.MM.DD form",
    )
    name: str | None = None
    dictionary_json: dict[str, Any] | None = Field(
        default=None,
        description="Inline candidate JSON payload to store before validation and conversion",
    )
    source_resource_filename: str | None = Field(
        default=None,
        min_length=1,
        description="Optional local JSON resource filename to load into the candidate",
    )

    @field_validator("ontology_family", "country")
    @classmethod
    def _normalize_candidate_slug(cls, value: str) -> str:
        return OntologyPublishRequest._normalize_slug(value)

    @field_validator("version")
    @classmethod
    def _validate_candidate_version(cls, value: str) -> str:
        return OntologyPublishRequest._validate_version(value)

    @field_validator("source_resource_filename")
    @classmethod
    def _validate_candidate_filename(cls, value: str | None) -> str | None:
        return OntologyPublishRequest._validate_resource_filename(value)

    @model_validator(mode="after")
    def _validate_source(self) -> OntologyDictionaryCandidateCreateRequest:
        if bool(self.dictionary_json) == bool(self.source_resource_filename):
            raise ValueError("Provide exactly one of dictionary_json or source_resource_filename")
        return self


class OntologyDictionaryCandidatePublishRequest(BaseModel):
    status: OntologyReleaseStatus = OntologyReleaseStatus.draft
    issued_at: date
    default_version: bool = False
    output_resource_filename: str | None = Field(
        default=None,
        min_length=1,
        description="Optional TTL resource filename to save before publish",
    )

    @field_validator("output_resource_filename")
    @classmethod
    def _validate_publish_candidate_filename(cls, value: str | None) -> str | None:
        return OntologyPublishRequest._validate_resource_filename(value)


class OntologyDictionaryCandidateSummary(BaseModel):
    id: str
    name: str
    ontology_family: str
    country: str
    version: str
    source_resource_filename: str | None = None
    source_dictionary_name: str | None = None
    source_dictionary_code: str | None = None
    source_dictionary_version: str | None = None
    created_at: str
    updated_at: str
    last_validation_report: OntologyDictionaryValidationReport | None = None


class OntologyDictionaryCandidate(OntologyDictionaryCandidateSummary):
    dictionary_json: dict[str, Any]


class OntologyDictionaryCandidateResponse(BaseModel):
    candidate: OntologyDictionaryCandidate


class OntologyDictionaryCandidateListResponse(BaseModel):
    count: int
    candidates: list[OntologyDictionaryCandidateSummary] = Field(default_factory=list)


class OntologyDictionaryCandidateSummaryResponse(BaseModel):
    id: str
    name: str
    ontology_family: str
    country: str
    version: str
    source_dictionary_name: str | None = None
    source_dictionary_code: str | None = None
    source_dictionary_version: str | None = None
    class_count: int | None = None
    subclass_count: int | None = None
    property_count: int | None = None
    datatype_property_count: int | None = None
    object_property_count: int | None = None
    class_property_link_count: int | None = None
    valid: bool | None = None
    completeness_ok: bool | None = None
    ttl_form_ok: bool | None = None
    publish_ready: bool
    warnings: list[str] = Field(default_factory=list)


class OntologyDeleteResponse(BaseModel):
    success: bool = True
    deleted: bool
    ontology_family: str
    country: str
    version: str
    graph_uri: str
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class OntologyVersionMetadata(BaseModel):
    version: str
    status: OntologyReleaseStatus
    issued_at: date
    is_latest: bool = False
    is_stable: bool = False
    is_default: bool = False
    url: str
    ttl_url: str
    graph_uri: str
    ontology_uri: str | None = None
    version_iri: str | None = None
    label: str | None = None
    description: str | None = None
    triple_count: int
    class_count: int = 0
    property_count: int = 0
    property_set_count: int = 0
    subclass_count: int = 0
    class_property_link_count: int = 0
    byte_size: int
    sha256: str
    imports: list[str] = Field(default_factory=list)
    resource_filename: str | None = None
    published_at: datetime


class OntologySummaryResponse(BaseModel):
    ontology_family: str
    country: str
    canonical_family_uri: str
    canonical_country_uri: str
    latest_version: str | None = None
    latest_stable_version: str | None = None
    default_version: str | None = None
    available_versions_url: str
    latest_url: str
    stable_url: str


class OntologyListResponse(BaseModel):
    count: int
    ontologies: list[OntologySummaryResponse] = Field(default_factory=list)


class OntologyVersionListResponse(BaseModel):
    ontology_family: str
    country: str
    canonical_family_uri: str
    canonical_country_uri: str
    versions: list[OntologyVersionMetadata] = Field(default_factory=list)


class OntologyVersionFetchResponse(BaseModel):
    ontology_family: str
    country: str
    canonical_family_uri: str
    canonical_country_uri: str
    version: OntologyVersionMetadata
    ttl: str


class OntologyVersionSummaryResponse(BaseModel):
    ontology_family: str
    country: str
    version: str
    ontology_uri: str | None = None
    version_iri: str | None = None
    title: str
    description: str | None = None
    status: OntologyReleaseStatus
    class_count: int
    property_count: int
    property_set_count: int
    subclass_count: int
    class_property_link_count: int
    publish_ready: bool
    warnings: list[str] = Field(default_factory=list)


class OntologySyncAction(StrEnum):
    publish = "publish"
    update = "update"
    current = "current"
    skip = "skip"
    failed = "failed"


class OntologySyncRequest(BaseModel):
    source_url: str | None = Field(
        default=None,
        description=(
            "URL of a registry index (default: the public firebim.org registry). "
            "Any registry serving the same JSON format is accepted."
        ),
    )
    families: list[str] | None = Field(default=None, description="Only sync these ontology families, e.g. ['fbo']")
    countries: list[str] | None = Field(default=None, description="Only sync these countries, e.g. ['dk', 'core']")
    selections: list[str] | None = Field(
        default=None,
        description="Explicit '<family>/<country>/<version>' identifiers to sync; overrides the other filters",
    )
    include_unlisted: bool = Field(default=True, description="Also sync entries the registry marks as unlisted")
    latest_only: bool = Field(default=False, description="Only sync the newest version of each ontology")
    overwrite: bool = Field(
        default=False,
        description="Re-publish versions that already exist locally but whose content or status differs",
    )
    dry_run: bool = Field(default=False, description="Only compute the plan; publish nothing")

    @field_validator("families", "countries")
    @classmethod
    def _normalize_slugs(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = sorted({item.strip().lower() for item in value if item and item.strip()})
        return normalized or None

    @field_validator("selections")
    @classmethod
    def _normalize_selections(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        for item in value:
            parts = [part.strip() for part in item.split("/")]
            if len(parts) != 3 or not all(parts):
                raise ValueError("selections must use '<family>/<country>/<version>'")
            normalized.append(f"{parts[0].lower()}/{parts[1].lower()}/{parts[2]}")
        return sorted(set(normalized)) or None

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        url = value.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("source_url must be an http(s) URL")
        return url


class OntologySyncItem(BaseModel):
    id: str = Field(description="'<family>/<country>/<version>'")
    ontology_family: str
    country: str
    version: str
    status: OntologyReleaseStatus
    issued_at: date
    kind: str = "ontology"
    visibility: str = "public"
    title: str | None = None
    ttl_url: str
    remote_sha256: str | None = None
    remote_byte_size: int | None = None
    is_latest: bool = False
    is_stable: bool = False
    is_default: bool = False
    local_exists: bool = False
    local_sha256: str | None = None
    local_status: OntologyReleaseStatus | None = None
    action: OntologySyncAction
    reason: str
    published: bool = False
    error: str | None = None


class OntologySyncResponse(BaseModel):
    success: bool = True
    source_url: str
    registry: str | None = None
    schema_version: int | None = None
    dry_run: bool
    fetched_at: datetime
    total: int
    to_publish: int
    to_update: int
    current: int
    skipped: int
    published: int
    failed: int
    items: list[OntologySyncItem] = Field(default_factory=list)


class OntologySyncJobStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class OntologySyncJobResponse(BaseModel):
    job_id: str
    status: OntologySyncJobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: OntologySyncResponse | None = None
    error: str | None = None


class OntologySyncSourceResponse(BaseModel):
    default_source_url: str
    registry_format: str = "firebim-ontology-registry"
    schema_version: int = 1
    documentation_url: str = "https://firebim.org/documentation/ontologies/#developers"
