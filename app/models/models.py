import uuid
from datetime import UTC, datetime

from sqlmodel import Column, DateTime, Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    authentik_sub: str = Field(unique=True, nullable=False, index=True)
    username: str = Field(unique=True, nullable=False)
    email: str = Field(nullable=False)
    hashed_password: str | None = Field(default=None, nullable=True)
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
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    )
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), onupdate=lambda: datetime.now(UTC)))


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
    ifc_model_id: str = Field(foreign_key="ifc_models.id", nullable=False)
    ids_id: str = Field(foreign_key="ids_files.id", nullable=False)
    task_id: str | None = Field(nullable=True)
    result_key: str | None = Field(nullable=True)
    result_json: str | None = Field(nullable=True, description="Store the result as JSON string")
    status: str = Field(default="pending", nullable=False, description="'pending', 'processing', 'completed', 'failed'")
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    )
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), onupdate=lambda: datetime.now(UTC)))
