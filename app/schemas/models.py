from typing import Any

from pydantic import BaseModel, Field


class IfcEntityCreate(BaseModel):
    id: str
    type: str
    globalId: str = Field(alias="globalId")
    name: str | None = None
    attributes: dict[str, Any] = {}

    class Config:
        populate_by_name = True


class IfcPropertyCreate(BaseModel):
    entity_id: str
    property_set_name: str
    property_name: str
    value: str
    value_type: str


class IfcRelationshipCreate(BaseModel):
    type: str
    relating_id: str
    related_id: str


class IfcSqlDataUpload(BaseModel):
    entities: list[IfcEntityCreate]
    properties: list[IfcPropertyCreate]
    relationships: list[IfcRelationshipCreate]
