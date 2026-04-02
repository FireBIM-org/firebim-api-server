from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CheckApiBaseResponse(BaseModel):
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False


class CheckRunOptions(BaseModel):
    inference: str = Field(default="rdfs", description="none | rdfs | owlrl | both")
    advanced: bool = True
    include_report_text: bool = True
    include_report_graph_ttl: bool = False
    include_targets: bool = True
    include_trace: bool = True
    debug: bool = False
    fail_fast: bool = False
    max_steps_per_target: int = 100


class CheckRunRequest(BaseModel):
    rule_id: str
    graph_id: str
    options: CheckRunOptions = Field(default_factory=CheckRunOptions)


class CheckTargetTraceStep(BaseModel):
    node_id: str
    node_type: str
    status: str = "Unknown"
    next_node_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class CheckTargetResult(BaseModel):
    target_node: str
    target_class: str | None = None
    status: str = "Unknown"
    message: str | None = None
    terminal_node_id: str | None = None
    trace: list[CheckTargetTraceStep] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class CheckRunSummary(BaseModel):
    total_targets: int = 0
    passed: int = 0
    failed: int = 0
    unknown: int = 0
    conforms: bool = False
    terminal_counts: dict[str, int] = Field(default_factory=dict)
    missing_data_categories: dict[str, int] = Field(default_factory=dict)


class CheckRunEngineInfo(BaseModel):
    engine: str = "compiled_runtime"
    inference: str = "rdfs"
    advanced: bool = True
    runtime: str | None = None
    evaluator: str | None = None
    target_resolver: str | None = None
    mode: str | None = None


class CheckRunResponse(CheckApiBaseResponse):
    rule_id: str
    graph_id: str
    summary: CheckRunSummary = Field(default_factory=CheckRunSummary)
    engine: CheckRunEngineInfo = Field(default_factory=CheckRunEngineInfo)
    targets: list[CheckTargetResult] = Field(default_factory=list)
    report_text: str | None = None
    report_graph_ttl: str | None = None
    debug: dict[str, Any] = Field(default_factory=dict)
    compiled_rule: dict[str, Any] = Field(default_factory=dict)


class BlindCheckRunOptions(BaseModel):
    include_targets: bool = True
    include_debug: bool = True
    max_steps: int = 100000


class BlindCheckRunRequest(BaseModel):
    rule_id: str
    graph_id: str
    options: BlindCheckRunOptions = Field(default_factory=BlindCheckRunOptions)


class BlindCheckTerminalOutput(BaseModel):
    target_node: str
    terminal_node_id: str | None = None
    terminal_value: str | None = None
    final_status: str = "Unknown"


class BlindCheckRunResponse(CheckApiBaseResponse):
    mode: str = "blind_shacl_construct"
    rule_id: str
    graph_id: str
    summary: CheckRunSummary = Field(default_factory=CheckRunSummary)
    end_outputs: list[BlindCheckTerminalOutput] = Field(default_factory=list)
    terminal_counts: dict[str, int] = Field(default_factory=dict)
    message_counts: dict[str, int] = Field(default_factory=dict)
    debug: dict[str, Any] = Field(default_factory=dict)


class CheckCompatibilityRequest(BaseModel):
    rule_id: str
    graph_id: str


class CompatibilityMatch(BaseModel):
    value: str
    count: int | None = None


class CompatibilityWarning(BaseModel):
    code: str
    message: str


class CheckCompatibilityResponse(CheckApiBaseResponse):
    rule_id: str
    graph_id: str
    compatibility_score: float = 0.0

    target_classes_required: list[str] = Field(default_factory=list)
    properties_required: list[str] = Field(default_factory=list)

    target_classes_found: list[CompatibilityMatch] = Field(default_factory=list)
    properties_found: list[CompatibilityMatch] = Field(default_factory=list)

    missing_target_classes: list[str] = Field(default_factory=list)
    missing_properties: list[str] = Field(default_factory=list)

    graph_classes_unused_by_rule: list[CompatibilityMatch] = Field(default_factory=list)
    graph_properties_unused_by_rule: list[CompatibilityMatch] = Field(
        default_factory=list
    )

    warnings: list[CompatibilityWarning] = Field(default_factory=list)
