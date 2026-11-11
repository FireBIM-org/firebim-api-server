import uuid
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Index, UniqueConstraint, text
from sqlmodel import Column, DateTime, Field, Relationship, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    authentik_sub: str = Field(unique=True, nullable=False, index=True)
    username: str = Field(unique=True, nullable=False)
    email: str = Field(nullable=False)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    )
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), onupdate=lambda: datetime.now(UTC)))


class IfcModel(SQLModel, table=True):
    __tablename__ = "ifc_models"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    filename: str = Field(nullable=False)
    s3_key: str = Field(nullable=False)
    user_id: str = Field(nullable=False)
    processed: bool = Field(default=False)
    task_id: str | None = Field(nullable=True)
    result_key: str | None = Field(nullable=True)
    processing_status: str = Field(default="pending", nullable=False)
    conversion_profile_id: str | None = Field(default=None, nullable=True)
    conversion_profile_name: str | None = Field(default=None, nullable=True)
    ingestion_backend: str = Field(default="legacy", nullable=False, sa_column_kwargs={"server_default": "legacy"})
    latest_job_id: str | None = None
    latest_successful_job_id: str | None = None
    latest_successful_graph_id: str | None = None
    latest_successful_revision_id: str | None = None
    deleted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    )
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), onupdate=lambda: datetime.now(UTC)))


class PublishedGraph(SQLModel, table=True):
    """Only committed, ready immutable baselines; artifacts stay outside legacy discovery."""

    __tablename__ = "published_graphs"

    id: str = Field(primary_key=True)
    graph_uri: str = Field(unique=True)
    user_id: str = Field(index=True)
    model_id: str = Field(foreign_key="ifc_models.id", index=True)
    job_id: str = Field(foreign_key="ingestion_jobs.id", unique=True)
    backend: str = Field(default="neo")
    status: str = Field(default="ready")
    profile_id: str
    revision_id: str
    revision_digest: str
    input_sha256: str
    runtime_fingerprint: str
    execution_fingerprint: str
    fencing_token: int
    attempt_number: int
    triple_count: int
    destination: dict = Field(sa_column=Column(JSON, nullable=False))
    provenance: dict = Field(sa_column=Column(JSON, nullable=False))
    ontology_artifacts: list[dict] = Field(sa_column=Column(JSON, nullable=False))
    manifest: dict = Field(sa_column=Column(JSON, nullable=False))
    report: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))


class IfcUploadSession(SQLModel, table=True):
    __tablename__ = "ifc_upload_sessions"
    __table_args__ = (UniqueConstraint("user_id", "request_id", name="uq_upload_owner_request"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    request_id: str
    request_digest: str
    model_id: str = Field(unique=True)
    filename: str
    staging_key: str = Field(unique=True)
    expected_size: int
    expected_sha256: str
    profile_id: str
    revision_id: str
    revision_digest: str
    revision_snapshot: dict = Field(sa_column=Column(JSON, nullable=False))
    runtime_fingerprint: str
    runtime_config: dict = Field(sa_column=Column(JSON, nullable=False))
    status: str = Field(default="uploading")
    job_id: str | None = Field(default=None, unique=True)
    raw_key: str | None = None
    raw_sha256: str | None = None
    raw_size: int | None = None
    source_schema: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class IngestionJob(SQLModel, table=True):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_ingestion_owner_request"),
        Index(
            "uq_ingestion_active_model",
            "model_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'retry_wait')"),
            sqlite_where=text("status IN ('queued', 'running', 'retry_wait')"),
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    model_id: str = Field(foreign_key="ifc_models.id", index=True)
    upload_session_id: str = Field(foreign_key="ifc_upload_sessions.id")
    request_id: str
    request_digest: str
    raw_key: str
    raw_sha256: str
    raw_size: int
    source_schema: str = Field(default="IFC4")
    profile_id: str
    revision_id: str
    revision_digest: str
    revision_snapshot: dict = Field(sa_column=Column(JSON, nullable=False))
    runtime_fingerprint: str
    runtime_config: dict = Field(sa_column=Column(JSON, nullable=False))
    status: str = Field(default="queued", index=True)
    stage: str = Field(default="admitted")
    dispatch_generation: int = Field(default=1)
    attempt_count: int = Field(default=0)
    max_attempts: int = Field(default=3)
    fencing_token: int = Field(default=0)
    lease_expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    cancellation_requested: bool = Field(default=False)
    retryable: bool = Field(default=False)
    result_graph_id: str | None = None
    report: dict = Field(
        default_factory=lambda: {"report_version": "neo-ingestion/1", "diagnostics": []},
        sa_column=Column(JSON, nullable=False),
    )
    checkpoints: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class IngestionAttempt(SQLModel, table=True):
    __tablename__ = "ingestion_attempts"
    __table_args__ = (UniqueConstraint("job_id", "number", name="uq_ingestion_attempt_number"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    job_id: str = Field(foreign_key="ingestion_jobs.id", index=True)
    number: int
    fencing_token: int
    status: str = Field(default="running")
    stage: str = Field(default="claimed")
    runtime_fingerprint: str
    diagnostics: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    artifacts: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class IngestionOutbox(SQLModel, table=True):
    __tablename__ = "ingestion_outbox"
    __table_args__ = (
        UniqueConstraint("job_id", "generation", name="uq_ingestion_outbox_generation"),
        UniqueConstraint("job_id", "request_id", name="uq_ingestion_outbox_request"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    job_id: str = Field(foreign_key="ingestion_jobs.id", index=True)
    generation: int = Field(default=1)
    request_id: str = Field(default="admission")
    task_name: str = Field(default="neo_ingestion")
    dispatch_attempts: int = Field(default=0)
    available_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),
        index=True,
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))
    delivered_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), index=True))
    last_error: str | None = None


class IdsFile(SQLModel, table=True):
    __tablename__ = "ids_files"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    filename: str = Field(nullable=False)
    s3_key: str = Field(nullable=False)
    user_id: str = Field(nullable=False)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    )
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), onupdate=lambda: datetime.now(UTC)))


class CheckResult(SQLModel, table=True):
    __tablename__ = "check_results"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    ifc_model_id: str = Field(sa_column=Column(ForeignKey("ifc_models.id", ondelete="CASCADE"), nullable=False))
    ids_id: str = Field(foreign_key="ids_files.id", nullable=False)
    task_id: str | None = Field(nullable=True)
    result_key: str | None = Field(nullable=True)
    result_json: str | None = Field(nullable=True, description="Store the result as JSON string")
    status: str = Field(default="pending", nullable=False, description="'pending', 'processing', 'completed', 'failed'")
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    )
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), onupdate=lambda: datetime.now(UTC)))


class IfcEntity(SQLModel, table=True):
    __tablename__ = "ifc_entities"
    __table_args__ = (UniqueConstraint("id", "model_id", name="uq_ifcentity_id_model_id"),)

    global_id: str = Field(primary_key=True)
    id: str = Field(index=True)
    model_id: str = Field(sa_column=Column(ForeignKey("ifc_models.id", ondelete="CASCADE"), nullable=False, index=True))
    parent_id: str | None = Field(
        default=None,
        sa_column=Column(ForeignKey("ifc_entities.global_id", ondelete="CASCADE"), index=True, nullable=True),
    )
    type: str = Field(nullable=False, index=True)
    name: str | None = Field(default=None, nullable=True)
    attributes: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False, server_default="{}"))

    parent: Optional["IfcEntity"] = Relationship(
        back_populates="children", sa_relationship_kwargs={"remote_side": "IfcEntity.global_id"}
    )
    children: list["IfcEntity"] = Relationship(back_populates="parent")
    properties: list["IfcProperty"] = Relationship(back_populates="entity")
    quantities: list["IfcQuantity"] = Relationship(back_populates="entity")


class IfcProperty(SQLModel, table=True):
    __tablename__ = "ifc_properties"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    entity_id: str = Field(
        sa_column=Column(ForeignKey("ifc_entities.global_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    property_set_name: str = Field(nullable=False, index=True)
    property_name: str = Field(nullable=False, index=True)
    value: str = Field(nullable=False)
    value_type: str = Field(nullable=False)

    entity: "IfcEntity" = Relationship(back_populates="properties")


class IfcQuantity(SQLModel, table=True):
    __tablename__ = "ifc_quantities"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    entity_id: str = Field(
        sa_column=Column(ForeignKey("ifc_entities.global_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    quantity_set_name: str = Field(nullable=False, index=True)
    quantity_name: str = Field(nullable=False, index=True)
    value: str = Field(nullable=False)
    value_type: str = Field(nullable=False)

    entity: "IfcEntity" = Relationship(back_populates="quantities")
