from typing import Any, List, Optional
from pydantic import BaseModel, Field


class RuleNodePayload(BaseModel):
    id: str
    node_type: str = "decision"

    target_class: Optional[str] = None
    property_path: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Any] = None

    true_path: Optional[str] = None
    false_path: Optional[str] = None
    unknown_path: Optional[str] = None

    label: Optional[str] = None
    config: dict = Field(default_factory=dict)


class RuleSetValidateRequest(BaseModel):
    id: str
    entry: str
    nodes: List[RuleNodePayload]


class RuleSetValidateResponse(BaseModel):
    valid: bool
    ruleset_id: str
    entry: Optional[str] = None
    node_count: int
    errors: List[str] = Field(default_factory=list)

class RuleSetCompileResponse(BaseModel):
    ruleset_id: str
    node_count: int
    ttl: str