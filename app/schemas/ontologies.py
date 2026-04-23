from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


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
        if value is None:
            return None
        ttl = value.strip()
        return ttl or None

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
