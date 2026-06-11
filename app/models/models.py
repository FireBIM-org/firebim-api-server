import uuid
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import JSON, ForeignKey, UniqueConstraint
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
