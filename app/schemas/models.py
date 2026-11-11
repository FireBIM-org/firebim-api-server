from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IfcEntityCreate(BaseModel):
    id: str
    type: str
    global_id: str = Field(alias="globalId")
    name: str | None = None
    attributes: dict[str, Any] = {}
    parent_id: str | None = Field(default=None, alias="parentId")

    class Config:
        populate_by_name = True


class IfcPropertyCreate(BaseModel):
    entity_id: str
    property_set_name: str
    property_name: str
    value: str
    value_type: str


class IfcQuantityCreate(BaseModel):
    entity_id: str
    quantity_set_name: str
    quantity_name: str
    value: str
    value_type: str


class IfcSqlDataUpload(BaseModel):
    entities: list[IfcEntityCreate] = []
    properties: list[IfcPropertyCreate] = []
    quantities: list[IfcQuantityCreate] = []
    conversion_profile_id: str | None = None
    conversion_profile_name: str | None = None


class IfcUploadInitiate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    filename: str = Field(min_length=5, max_length=255)
    size_bytes: int = Field(gt=0, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str = Field(min_length=1, max_length=200)
    revision_id: str = Field(min_length=1, max_length=200)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value):
        if (
            not value.lower().endswith(".ifc")
            or any(char in value for char in ("/", "\\"))
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("Expected an IFC filename without a path or control characters")
        return value


class IfcUploadComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IfcUploadResponse(BaseModel):
    id: str
    model_id: str
    status: Literal["uploading", "completed"]
    expires_at: datetime
    upload_method: Literal["PUT"] = "PUT"
    upload_url: str | None = None
    upload_headers: dict[str, str] = Field(default_factory=dict)
    job_id: str | None = None


class IngestionRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    expected_dispatch_generation: int = Field(ge=1)


class IngestionReprocessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    profile_id: str = Field(min_length=1, max_length=200)
    revision_id: str = Field(min_length=1, max_length=200)


class IngestionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    model_id: str
    status: Literal["queued", "running", "retry_wait", "succeeded", "failed", "cancelled"]
    stage: str
    profile_id: str
    revision_id: str
    revision_digest: str
    runtime_fingerprint: str
    raw_sha256: str
    raw_size: int
    source_schema: str
    dispatch_generation: int
    attempt_count: int
    max_attempts: int
    cancellation_requested: bool
    retryable: bool
    result_graph_id: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class IngestionReportResponse(BaseModel):
    job_id: str
    report_version: str
    diagnostics: list[dict[str, Any]]
    attempts: list[dict[str, Any]]


class IngestionRevisionResponse(BaseModel):
    profile_id: str
    profile_name: str
    revision_id: str
    revision_digest: str
    published_at: str


class IngestionRevisionsResponse(BaseModel):
    revisions: list[IngestionRevisionResponse]
    count: int
    max_input_bytes: int
    runtime_fingerprint: str
