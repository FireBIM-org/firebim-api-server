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
    diagnostics: bool = False
    fail_fast: bool = False
    max_steps_per_target: int = 100
    solver: str | None = None
    planner_strategy: str = Field(default="auto", description="auto | forward | backward")
    include_planner_debug: bool = False
    include_end_node_targets: bool = False
    diagnostic_terminal_values: list[str] = Field(default_factory=list)
    diagnostic_end_node_ids: list[str] = Field(default_factory=list)
    diagnostic_analysis_codes: list[str] = Field(default_factory=list)
    diagnostic_success_only: bool = False
    persist_derived: bool = Field(
        default=False,
        description="Publish values written by derive elements to the derived named graph of this graph/rule",
    )
    use_derived_from: list[str] = Field(
        default_factory=list,
        description="Rule ids whose persisted derived graphs are merged into the building graph before evaluation",
    )
    target_nodes: list[str] = Field(
        default_factory=list,
        description=(
            "Restrict the run to these target IRIs (used by the RuleBook to chain a rule only for the targets "
            "that reached an end element with next_rule)"
        ),
    )
    schema: str = Field(
        default="auto",
        description=(
            "Which published ontologies' schema triples (subClassOf, subPropertyOf, equivalentClass/Property, "
            "inverseOf) are merged into the building graph before evaluation: 'auto' = every ontology whose "
            "namespace the rule uses, 'none' = no merge, or a comma-separated list of family/country[/version]"
        ),
    )


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
    engine: str = "standard_compiled_runtime"
    inference: str = "rdfs"
    advanced: bool = True
    runtime: str | None = None
    evaluator: str | None = None
    target_resolver: str | None = None
    mode: str | None = None
    solver_mode: str | None = None
    planner_strategy: str | None = None
    diagnostics_enabled: bool = False


class CheckEndNodeSummary(BaseModel):
    end_node_id: str
    terminal_value: str | None = None
    terminal_kind: str | None = None
    label: str | None = None
    output_text: str | None = None
    analysis_code: str | None = None
    next_rule: str | None = Field(default=None, description="end_kind 'rule': rule that continues for these targets")
    count: int = 0


class CheckEndNodeTargets(BaseModel):
    end_node_id: str
    target_nodes: list[str] = Field(default_factory=list)


class CheckDerivedValue(BaseModel):
    target: str
    property: str
    value: Any = None
    node_id: str | None = None
    expression: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    overwrote_previous: Any = None


class CheckSimpleRunResponse(CheckApiBaseResponse):
    rule_id: str
    graph_id: str
    solver: str
    start_node_id: str | None = None
    summary: CheckRunSummary = Field(default_factory=CheckRunSummary)
    end_nodes: list[CheckEndNodeSummary] = Field(default_factory=list)
    end_node_targets: list[CheckEndNodeTargets] = Field(default_factory=list)
    engine: CheckRunEngineInfo = Field(default_factory=CheckRunEngineInfo)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    report_text: str | None = None
    debug: dict[str, Any] = Field(default_factory=dict)
    derived_values: list[CheckDerivedValue] = Field(default_factory=list)
    derived_graph_uri: str | None = None
    derived_persisted: bool = False


class CheckDiagnosticTimingGroups(BaseModel):
    request_ms: dict[str, float] = Field(default_factory=dict)
    rule_ms: dict[str, float] = Field(default_factory=dict)
    per_node_ms: dict[str, float] = Field(default_factory=dict)
    per_class_ms: dict[str, float] = Field(default_factory=dict)
    per_transition_ms: dict[str, float] = Field(default_factory=dict)
    per_completed_chain_ms: list[dict[str, Any]] = Field(default_factory=list)


class CheckDiagnosticResponse(CheckSimpleRunResponse):
    compiled_rule: dict[str, Any] = Field(default_factory=dict)
    targets: list[CheckTargetResult] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    timings: CheckDiagnosticTimingGroups = Field(default_factory=CheckDiagnosticTimingGroups)
    planner: dict[str, Any] = Field(default_factory=dict)
    transition_inspections: list[dict[str, Any]] = Field(default_factory=list)
    node_inspections: list[dict[str, Any]] = Field(default_factory=list)
    terminal_failures: list[dict[str, Any]] = Field(default_factory=list)
    sequence_events: list[dict[str, Any]] = Field(default_factory=list)
    completed_chains: list[dict[str, Any]] = Field(default_factory=list)


class CheckRunResponse(CheckSimpleRunResponse):
    targets: list[CheckTargetResult] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    report_graph_ttl: str | None = None
    compiled_rule: dict[str, Any] = Field(default_factory=dict)


class BlindCheckRunOptions(BaseModel):
    include_targets: bool = True
    include_debug: bool = True
    max_steps: int = 100000
    schema: str = Field(default="auto", description="see CheckRunOptions.schema")
    use_derived_from: list[str] = Field(default_factory=list, description="see CheckRunOptions.use_derived_from")
    target_nodes: list[str] = Field(default_factory=list, description="see CheckRunOptions.target_nodes")


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
    derived_values: list[CheckDerivedValue] = Field(default_factory=list)
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
    graph_properties_unused_by_rule: list[CompatibilityMatch] = Field(default_factory=list)

    warnings: list[CompatibilityWarning] = Field(default_factory=list)
