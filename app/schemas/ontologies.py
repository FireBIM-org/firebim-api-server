from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ReleaseStatus = Literal["draft", "candidate", "stable", "deprecated", "withdrawn"]
RELEASE_ID_PATTERN = r"^\d{4}\.\d{2}$"


class OntologyVersionManifestEntry(BaseModel):
    version: str = Field(..., min_length=1, pattern=RELEASE_ID_PATTERN)
    status: ReleaseStatus
    issued_at: str = Field(..., min_length=1)
    object_key: str | None = None
    content_type: str = "text/turtle; charset=utf-8"
    size_bytes: int | None = Field(default=None, ge=0)
    checksum_sha256: str | None = None


class OntologyManifest(BaseModel):
    ontology_family: str = Field(..., min_length=1)
    country: str = Field(..., min_length=1)
    default_version: str | None = Field(default=None, pattern=RELEASE_ID_PATTERN)
    versions: list[OntologyVersionManifestEntry] = Field(default_factory=list)


class OntologiesHealthResponse(BaseModel):
    status: str
    storage_connected: bool
    storage_backend: str | None = None
    storage_host: str | None = None
    root_prefix: str


class OntologyVersionListItem(BaseModel):
    version: str
    status: str
    issued_at: str
    is_latest: bool
    is_stable: bool
    is_default: bool = False
    url: str
    content_url: str
    content_type: str = "text/turtle; charset=utf-8"
    size_bytes: int | None = None
    checksum_sha256: str | None = None


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
    versions: list[OntologyVersionListItem] = Field(default_factory=list)


class OntologyVersionResponse(BaseModel):
    ontology_family: str
    country: str
    canonical_family_uri: str
    canonical_country_uri: str
    version: str = Field(..., pattern=RELEASE_ID_PATTERN)
    status: ReleaseStatus
    issued_at: str
    is_latest: bool
    is_stable: bool
    is_default: bool = False
    url: str
    content_url: str
    content_type: str = "text/turtle; charset=utf-8"
    size_bytes: int | None = None
    checksum_sha256: str | None = None


class OntologyPublishRequest(BaseModel):
    ontology_family: str = Field(..., min_length=1)
    country: str = Field(..., min_length=1)
    version: str = Field(..., pattern=RELEASE_ID_PATTERN)
    status: ReleaseStatus
    issued_at: str = Field(..., min_length=1, examples=["2026-03-01"])
    ttl: str = Field(..., min_length=1, examples=["@prefix fbo: <https://firebim.org/ontology/fbo/dk#> ."])
    content_type: str = "text/turtle; charset=utf-8"
    default_version: bool = False


class OntologyPublishResponse(OntologyVersionResponse):
    created: bool
    manifest_updated: bool = True


class OntologyDeleteResponse(BaseModel):
    ontology_family: str
    country: str
    version: str = Field(..., pattern=RELEASE_ID_PATTERN)
    deleted: bool = True
    deleted_content: bool = True
    manifest_updated: bool = True
    remaining_versions: int = Field(default=0, ge=0)
