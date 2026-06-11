from typing import Any

from pydantic import BaseModel, Field


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
