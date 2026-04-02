from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CheckStatus = Literal["True", "False", "Unknown"]
NodeType = Literal["start", "decision", "end"]


class RunOptions(BaseModel):
    inference: str = Field(default="rdfs", description="none | rdfs | owlrl | both")
    advanced: bool = True
    debug: bool = False

    include_targets: bool = True
    include_trace: bool = True
    include_report_text: bool = True
    include_report_graph_ttl: bool = False

    fail_fast: bool = False
    max_steps_per_target: int = 100


class GraphRef(BaseModel):
    graph_id: str
    graph_uri: str
    ttl: str | None = None


class EvalContext(BaseModel):
    rule_id: str
    graph_id: str
    graph_uri: str
    inference: str = "rdfs"
    advanced: bool = True
    debug: bool = False

    current_step_index: int = 0
    visited_node_ids: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class AtomicOutcome(BaseModel):
    status: CheckStatus
    reason_code: str | None = None
    message: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    raw_report_ttl: str | None = None


class BaseCompiledNode(BaseModel):
    node_id: str
    node_type: NodeType
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class StartNode(BaseCompiledNode):
    node_type: Literal["start"] = "start"
    next_node_id: str | None = None


class DecisionNode(BaseCompiledNode):
    node_type: Literal["decision"] = "decision"

    target_class: str | None = None
    property_path: str | None = None
    operator: str | None = None
    value: Any = None

    true_node_id: str | None = None
    false_node_id: str | None = None
    unknown_node_id: str | None = None

    shape_id: str | None = None
    shape_uri: str | None = None
    sparql_construct: str | None = None


class EndNode(BaseCompiledNode):
    node_type: Literal["end"] = "end"
    end_kind: str | None = None
    output_text: str | None = None


CompiledNode = StartNode | DecisionNode | EndNode


class CompiledRuleSet(BaseModel):
    rule_id: str
    entry_node_id: str
    nodes: dict[str, CompiledNode] = Field(default_factory=dict)

    source_rule_ttl: str | None = None
    source_graph_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def get_node(self, node_id: str) -> CompiledNode:
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            raise KeyError(
                f"Compiled rule '{self.rule_id}' is missing node '{node_id}'"
            ) from exc


class TargetRef(BaseModel):
    target_node: str
    target_class: str | None = None


class TargetTraceStep(BaseModel):
    node_id: str
    node_type: str
    status: CheckStatus
    next_node_id: str | None
    evidence: dict[str, Any] = Field(default_factory=dict)


class TargetTrace(BaseModel):
    target_node: str
    target_class: str | None = None
    final_status: CheckStatus = "Unknown"
    terminal_node_id: str | None = None
    message: str | None = None
    trace: list[TargetTraceStep] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class RuleRunSummary(BaseModel):
    total_targets: int = 0
    passed: int = 0
    failed: int = 0
    unknown: int = 0
    conforms: bool = False

    terminal_counts: dict[str, int] = Field(default_factory=dict)
    missing_data_categories: dict[str, int] = Field(default_factory=dict)


class RuleRunResult(BaseModel):
    rule_id: str
    graph_id: str
    summary: RuleRunSummary = Field(default_factory=RuleRunSummary)
    targets: list[TargetTrace] = Field(default_factory=list)

    compiled_rule: dict[str, Any] = Field(default_factory=dict)
    engine: dict[str, Any] = Field(default_factory=dict)
    debug: dict[str, Any] = Field(default_factory=dict)

    report_text: str | None = None
    report_graph_ttl: str | None = None


class CheckRunResult(BaseModel):
    graph_id: str
    rule_results: list[RuleRunResult] = Field(default_factory=list)

    total_rules: int = 0
    total_targets: int = 0
    passed: int = 0
    failed: int = 0
    unknown: int = 0

    debug: dict[str, Any] = Field(default_factory=dict)
