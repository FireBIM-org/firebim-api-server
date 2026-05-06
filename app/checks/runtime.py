from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.checks.caching import accumulate_timing, increment_counter
from app.checks.evaluator_base import AtomicEvaluator
from app.checks.execution_state import BindingFrame, DiagnosticsCollector, ExecutionContext
from app.checks.pathways import PathwayResolver
from app.checks.planner import RuleExecutionPlanner
from app.checks.result_models import (
    CheckStatus,
    CompiledRuleSet,
    DecisionNode,
    EndNode,
    EvalContext,
    GraphRef,
    RuleRunResult,
    RuleRunSummary,
    RunOptions,
    StartNode,
    TargetRef,
    TargetTrace,
    TargetTraceStep,
)
from app.checks.targeting import TargetResolver


@dataclass
class _TraversalState:
    node_id: str
    target_node: str
    target_class: str | None
    steps: list[TargetTraceStep]
    visited_node_ids: list[str]
    depth: int
    binding_frame: BindingFrame = field(default_factory=BindingFrame)


class CheckRuntime:
    def __init__(
        self,
        *,
        evaluator: AtomicEvaluator,
        target_resolver: TargetResolver | None = None,
        pathway_resolver: PathwayResolver | None = None,
        planner: RuleExecutionPlanner | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.target_resolver = target_resolver or TargetResolver()
        self.pathway_resolver = pathway_resolver or PathwayResolver(cache=self.target_resolver.cache)
        self.planner = planner or RuleExecutionPlanner()
        self._active_execution_context: ExecutionContext | None = None

    def run_rule(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        options: RunOptions | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> RuleRunResult:
        options = options or RunOptions()
        first_decision = self._resolve_entry_decision(compiled)
        execution_context = self._build_execution_context(
            graph_ref=graph_ref,
            compiled=compiled,
            options=options,
            metrics=metrics,
        )
        self._active_execution_context = execution_context

        started_targets = perf_counter()
        initial_targets = self.target_resolver.resolve_targets_for_decision(
            graph_ref=graph_ref,
            node=first_decision,
            graph_index=execution_context.graph_index,
            metrics=metrics,
        )
        accumulate_timing(metrics, "resolve_targets_total", perf_counter() - started_targets)
        execution_context.metrics.record_active_targets(first_decision.node_id, len(initial_targets))

        traces: list[TargetTrace] = []
        for target in initial_targets:
            traces.extend(
                self._traverse_from_target(
                    graph_ref=graph_ref,
                    compiled=compiled,
                    initial_node_id=first_decision.node_id,
                    target_node=target.target_node,
                    target_class=target.target_class,
                    options=options,
                    metrics=metrics,
                )
            )

        started_summary = perf_counter()
        summary = self._build_summary(traces)
        accumulate_timing(metrics, "build_result", perf_counter() - started_summary)

        result = RuleRunResult(
            rule_id=compiled.rule_id,
            graph_id=graph_ref.graph_id,
            summary=summary,
            targets=traces if options.include_targets else [],
            compiled_rule=self._compiled_rule_payload(compiled),
            engine={
                "runtime": self.__class__.__name__,
                "evaluator": self.evaluator.__class__.__name__,
                "target_resolver": self.target_resolver.__class__.__name__,
                "mode": "standard_compiled_runtime",
                "planner_strategy": execution_context.plan.strategy,
                "diagnostics_enabled": options.diagnostics,
            },
            debug={
                "initial_target_count": len(initial_targets),
                "initial_target_node_id": first_decision.node_id,
                "include_trace": options.include_trace,
                "include_targets": options.include_targets,
                "max_steps_per_target": options.max_steps_per_target,
                **(execution_context.schema.to_debug_dict() if options.diagnostics else {}),
                **execution_context.metrics.to_debug_dict(),
            },
        )
        if options.include_planner_debug:
            result.debug["planner"] = execution_context.plan.debug

        if not options.include_report_text:
            result.report_text = None
        if not options.include_report_graph_ttl:
            result.report_graph_ttl = None
        self._active_execution_context = None
        return result

    def _resolve_entry_decision(self, compiled: CompiledRuleSet) -> DecisionNode:
        entry_node = compiled.get_node(compiled.entry_node_id)
        if entry_node.node_type == "decision":
            return entry_node
        if entry_node.node_type == "start":
            return self._resolve_first_decision_from_start(compiled, entry_node)
        raise ValueError(
            f"Compiled rule '{compiled.rule_id}' entry node '{compiled.entry_node_id}' "
            f"must be a start or decision node, got '{entry_node.node_type}'"
        )

    def _resolve_first_decision_from_start(self, compiled: CompiledRuleSet, start_node: StartNode) -> DecisionNode:
        if not start_node.next_node_id:
            raise ValueError(f"Start node '{start_node.node_id}' has no next_node_id")
        next_node = compiled.get_node(start_node.next_node_id)
        if next_node.node_type != "decision":
            raise ValueError(f"First executable node '{next_node.node_id}' must be a decision node")
        return next_node

    def _build_execution_context(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ) -> ExecutionContext:
        graph_index = self.target_resolver.get_graph_index(graph_ref, metrics=metrics)
        plan = self.planner.plan(
            compiled=compiled,
            schema=graph_index.schema,
            options=options,
            metrics=metrics,
        )
        return ExecutionContext(
            graph_ref=graph_ref,
            compiled=compiled,
            options=options,
            graph_cache_key=graph_index.graph_cache_key,
            graph_index=graph_index,
            schema=graph_index.schema,
            plan=plan,
            metrics=DiagnosticsCollector(enabled=options.diagnostics),
        )

    def _compiled_rule_payload(self, compiled: CompiledRuleSet) -> dict[str, Any]:
        return {
            "rule_id": compiled.rule_id,
            "entry_node_id": compiled.entry_node_id,
            "start_node_id": compiled.entry_node_id,
            "node_count": len(compiled.nodes),
            "metadata": compiled.metadata,
            "end_nodes": [
                {
                    "node_id": node.node_id,
                    "label": node.label,
                    "end_kind": getattr(node, "end_kind", None),
                    "output_text": getattr(node, "output_text", None),
                    "terminal_value": node.config.get("terminal_value") or node.config.get("value"),
                    "analysis_code": node.config.get("analysis_code"),
                }
                for node in compiled.nodes.values()
                if node.node_type == "end"
            ],
        }

    def _make_initial_binding(self, node_id: str, target_node: str) -> BindingFrame:
        return BindingFrame().with_binding(node_id, target_node)

    def _traverse_from_target(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        initial_node_id: str,
        target_node: str,
        target_class: str | None,
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ) -> list[TargetTrace]:
        initial_state = _TraversalState(
            node_id=initial_node_id,
            target_node=target_node,
            target_class=target_class,
            steps=[],
            visited_node_ids=[],
            depth=0,
            binding_frame=BindingFrame().with_binding(initial_node_id, target_node),
        )
        traces: list[TargetTrace] = []
        queue: list[_TraversalState] = [initial_state]

        while queue:
            state = queue.pop(0)
            if state.depth >= options.max_steps_per_target:
                traces.append(
                    self._make_terminal_trace(
                        target_node=state.target_node,
                        target_class=state.target_class,
                        final_status="Unknown",
                        terminal_node_id=None,
                        message=(
                            f"Traversal stopped for target '{state.target_node}' because "
                            f"max_steps_per_target={options.max_steps_per_target} was reached"
                        ),
                        steps=state.steps,
                        evidence={"reason_code": "max_steps_reached", "visited_node_ids": state.visited_node_ids},
                        include_trace=options.include_trace,
                    )
                )
                if options.fail_fast:
                    break
                continue

            if state.node_id in state.visited_node_ids:
                traces.append(
                    self._make_terminal_trace(
                        target_node=state.target_node,
                        target_class=state.target_class,
                        final_status="Unknown",
                        terminal_node_id=state.node_id,
                        message=(
                            f"Traversal cycle detected at node '{state.node_id}' for target '{state.target_node}'"
                        ),
                        steps=state.steps,
                        evidence={
                            "reason_code": "cycle_detected",
                            "visited_node_ids": state.visited_node_ids + [state.node_id],
                        },
                        include_trace=options.include_trace,
                    )
                )
                if options.fail_fast:
                    break
                continue

            node = compiled.get_node(state.node_id)
            if node.node_type == "end":
                terminal_status = self._infer_end_status(node, incoming_status=self._last_status(state.steps))
                end_step = TargetTraceStep(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    status=terminal_status,
                    next_node_id=None,
                    evidence={"end_kind": node.end_kind, "output_text": node.output_text},
                )
                traces.append(
                    self._make_terminal_trace(
                        target_node=state.target_node,
                        target_class=state.target_class,
                        final_status=terminal_status,
                        terminal_node_id=node.node_id,
                        message=self._end_message(node),
                        steps=state.steps + [end_step],
                        evidence={"reason_code": "end_node_reached", "end_kind": node.end_kind},
                        include_trace=options.include_trace,
                    )
                )
                if options.fail_fast:
                    break
                continue

            if node.node_type != "decision":
                traces.append(
                    self._make_terminal_trace(
                        target_node=state.target_node,
                        target_class=state.target_class,
                        final_status="Unknown",
                        terminal_node_id=node.node_id,
                        message=f"Unsupported executable node type '{node.node_type}'",
                        steps=state.steps,
                        evidence={"reason_code": "unsupported_node_type", "node_type": node.node_type},
                        include_trace=options.include_trace,
                    )
                )
                if options.fail_fast:
                    break
                continue

            decision = node
            if self._active_execution_context is not None:
                self._active_execution_context.metrics.record_node_visit(
                    decision.node_id,
                    node_type=decision.node_type,
                    target_class=decision.target_class,
                    property_path=decision.property_path,
                    sample_target=state.target_node,
                )
            started_eval = perf_counter()
            outcome = self.evaluator.evaluate(
                graph_ref=graph_ref,
                node=decision,
                target=state.target_node,
                ctx=EvalContext(
                    rule_id=compiled.rule_id,
                    graph_id=graph_ref.graph_id,
                    graph_uri=graph_ref.graph_uri,
                    inference=options.inference,
                    advanced=options.advanced,
                    debug=options.debug,
                    current_step_index=state.depth,
                    visited_node_ids=list(state.visited_node_ids),
                    extra={
                        "include_report_text": options.include_report_text,
                        "include_report_graph_ttl": options.include_report_graph_ttl,
                        "metrics": metrics or {},
                        "graph_index": self._active_execution_context.graph_index
                        if self._active_execution_context
                        else None,
                        "execution_context": self._active_execution_context,
                        "binding_frame": state.binding_frame.bindings,
                    },
                ),
            )
            eval_duration_seconds = perf_counter() - started_eval
            eval_duration_ms = round(eval_duration_seconds * 1000.0, 3)
            accumulate_timing(metrics, "evaluate_node", eval_duration_seconds)
            increment_counter(metrics, "evaluated_nodes")
            if self._active_execution_context is not None:
                self._active_execution_context.metrics.record_node_timing(
                    decision.node_id,
                    duration_ms=eval_duration_ms,
                    target_class=decision.target_class,
                    target_count=1,
                )

            next_node_id = self._branch_next_node_id(decision, outcome.status)
            step_evidence = dict(outcome.evidence or {})
            if outcome.reason_code:
                step_evidence["reason_code"] = outcome.reason_code
            if outcome.message:
                step_evidence["message"] = outcome.message
            if options.diagnostics:
                step_evidence["duration_ms"] = eval_duration_ms
            step = TargetTraceStep(
                node_id=decision.node_id,
                node_type=decision.node_type,
                status=outcome.status,
                next_node_id=next_node_id,
                evidence=step_evidence,
            )

            if not next_node_id:
                traces.append(
                    self._make_terminal_trace(
                        target_node=state.target_node,
                        target_class=state.target_class,
                        final_status=outcome.status,
                        terminal_node_id=decision.node_id,
                        message=(
                            f"Decision node '{decision.node_id}' has no outgoing branch for outcome '{outcome.status}'"
                        ),
                        steps=state.steps + [step],
                        evidence={
                            "reason_code": "missing_branch_target",
                            "decision_outcome": outcome.status,
                            "decision_message": outcome.message,
                            "decision_reason_code": outcome.reason_code,
                        },
                        include_trace=options.include_trace,
                    )
                )
                if options.fail_fast:
                    break
                continue

            next_node = compiled.get_node(next_node_id)
            next_states = self._resolve_next_states(
                graph_ref=graph_ref,
                current_target_node=state.target_node,
                current_target_class=state.target_class,
                next_node=next_node,
                current_steps=state.steps + [step],
                visited_node_ids=state.visited_node_ids + [decision.node_id],
                depth=state.depth + 1,
                current_node=decision,
                binding_frame=state.binding_frame.with_binding(decision.node_id, state.target_node),
                metrics=metrics,
            )
            if not next_states:
                failure_details = self._build_no_targets_debug(
                    current_node=decision,
                    next_node=next_node,
                    current_target_node=state.target_node,
                )
                if self._active_execution_context is not None:
                    self._active_execution_context.metrics.record_terminal_failure(failure_details)
                traces.append(
                    self._make_terminal_trace(
                        target_node=state.target_node,
                        target_class=state.target_class,
                        final_status="Unknown",
                        terminal_node_id=next_node_id,
                        message=(
                            f"No candidate targets resolved for next node '{next_node_id}' "
                            f"after decision '{decision.node_id}'"
                        ),
                        steps=state.steps + [step],
                        evidence={
                            "reason_code": "no_targets_for_next_node",
                            "decision_outcome": outcome.status,
                            "next_node_id": next_node_id,
                            **failure_details,
                        },
                        include_trace=options.include_trace,
                    )
                )
                if options.fail_fast:
                    break
                continue

            queue.extend(next_states)
        return traces

    def _resolve_next_states(
        self,
        *,
        graph_ref: GraphRef,
        current_target_node: str,
        current_target_class: str | None,
        current_node,
        next_node,
        current_steps: list[TargetTraceStep],
        visited_node_ids: list[str],
        depth: int,
        binding_frame: BindingFrame,
        metrics: dict[str, Any] | None = None,
    ) -> list[_TraversalState]:
        if next_node.node_type == "end":
            return [
                _TraversalState(
                    next_node.node_id,
                    current_target_node,
                    current_target_class,
                    current_steps,
                    visited_node_ids,
                    depth,
                    binding_frame,
                )
            ]
        if next_node.node_type != "decision":
            return [
                _TraversalState(
                    next_node.node_id,
                    current_target_node,
                    current_target_class,
                    current_steps,
                    visited_node_ids,
                    depth,
                    binding_frame,
                )
            ]
        if current_node.node_type != "decision":
            return [
                _TraversalState(
                    next_node.node_id,
                    current_target_node,
                    current_target_class,
                    current_steps,
                    visited_node_ids,
                    depth,
                    binding_frame,
                )
            ]

        execution_context = self._active_execution_context
        if execution_context is None:
            raise RuntimeError("Execution context is not initialized")

        resolved_batch = self.pathway_resolver.resolve_forward(
            current_targets=[TargetRef(target_node=current_target_node, target_class=current_target_class)],
            from_node=current_node,
            to_node=next_node,
            execution_context=execution_context,
            metrics=metrics,
        )
        resolved_targets = resolved_batch.targets
        return [
            _TraversalState(
                next_node.node_id,
                item.target_node,
                item.target_class,
                current_steps,
                visited_node_ids,
                depth,
                binding_frame.with_binding(next_node.node_id, item.target_node),
            )
            for item in resolved_targets
        ]

    def _build_no_targets_debug(
        self,
        *,
        current_node: DecisionNode,
        next_node,
        current_target_node: str,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {
            "from_node_id": current_node.node_id,
            "from_class": current_node.target_class,
            "from_property": current_node.property_path,
            "current_target_node": current_target_node,
            "next_node_id": getattr(next_node, "node_id", None),
            "next_node_type": getattr(next_node, "node_type", None),
            "next_class": getattr(next_node, "target_class", None),
            "next_property": getattr(next_node, "property_path", None),
        }
        execution_context = self._active_execution_context
        if execution_context is not None:
            transition_id = f"{current_node.node_id}->{getattr(next_node, 'node_id', None)}:forward"
            matching = [
                item
                for item in execution_context.metrics.transition_inspections
                if item.get("transition_id") == transition_id
            ]
            if matching:
                details["transition_inspection"] = matching[-1]
        return details

    def _branch_next_node_id(self, node: DecisionNode, status: CheckStatus) -> str | None:
        if status == "True":
            return node.true_node_id
        if status == "False":
            return node.false_node_id
        return node.unknown_node_id

    def _build_summary(self, traces: Iterable[TargetTrace]) -> RuleRunSummary:
        traces = list(traces)
        summary = RuleRunSummary(
            total_targets=len(traces),
            passed=sum(1 for trace in traces if trace.final_status == "True"),
            failed=sum(1 for trace in traces if trace.final_status == "False"),
            unknown=sum(1 for trace in traces if trace.final_status == "Unknown"),
            conforms=all(trace.final_status == "True" for trace in traces) if traces else False,
            terminal_counts={},
            missing_data_categories={},
        )
        for trace in traces:
            terminal_key = trace.terminal_node_id or "__no_terminal__"
            summary.terminal_counts[terminal_key] = summary.terminal_counts.get(terminal_key, 0) + 1
            reason_code = self._extract_missing_data_reason(trace)
            if reason_code:
                summary.missing_data_categories[reason_code] = summary.missing_data_categories.get(reason_code, 0) + 1
        return summary

    def _extract_missing_data_reason(self, trace: TargetTrace) -> str | None:
        if trace.final_status != "Unknown":
            return None
        reason_code = trace.evidence.get("reason_code")
        if reason_code:
            return str(reason_code)
        for step in trace.trace:
            step_reason = step.evidence.get("reason_code")
            if step_reason:
                return str(step_reason)
        return None

    def _make_terminal_trace(
        self,
        *,
        target_node: str,
        target_class: str | None,
        final_status: CheckStatus,
        terminal_node_id: str | None,
        message: str | None,
        steps: list[TargetTraceStep],
        evidence: dict,
        include_trace: bool,
    ) -> TargetTrace:
        trace = TargetTrace(
            target_node=target_node,
            target_class=target_class,
            final_status=final_status,
            terminal_node_id=terminal_node_id,
            message=message,
            trace=steps if include_trace else [],
            evidence=evidence,
        )
        if self._active_execution_context is not None and self._active_execution_context.options.diagnostics:
            self._active_execution_context.metrics.record_chain_completion(
                {
                    "target_node": target_node,
                    "terminal_node_id": terminal_node_id,
                    "final_status": final_status,
                    "node_sequence": [step.node_id for step in steps],
                    "duration_ms": round(
                        sum(float(step.evidence.get("duration_ms", 0.0)) for step in steps),
                        3,
                    ),
                }
            )
        return trace

    def _last_status(self, steps: list[TargetTraceStep]) -> CheckStatus:
        if not steps:
            return "Unknown"
        return steps[-1].status

    def _infer_end_status(self, node: EndNode, incoming_status: CheckStatus) -> CheckStatus:
        terminal_value = node.config.get("terminal_value") or node.config.get("value")
        if terminal_value is not None:
            terminal_text = str(terminal_value).strip().lower()
            if terminal_text in {"pass", "passed", "true", "success"}:
                return "True"
            if terminal_text in {"fail", "failed", "false", "error"}:
                return "False"
            if terminal_text in {"unknown", "missing_data", "missing-data"}:
                return "Unknown"
        text = (self._end_message(node) or "").strip().lower()
        if node.end_kind:
            end_kind = node.end_kind.strip().lower()
            if end_kind in {"pass", "passed", "true", "success"}:
                return "True"
            if end_kind in {"fail", "failed", "false", "error"}:
                return "False"
            if end_kind in {"unknown", "missing_data", "missing-data"}:
                return "Unknown"
        if "missing data" in text or "cannot be evaluated" in text or "unknown" in text:
            return "Unknown"
        if "did not pass" in text or "failed" in text or "not pass" in text:
            return "False"
        if "passed" in text or text == "pass":
            return "True"
        return incoming_status

    def _end_message(self, node: EndNode) -> str | None:
        if node.output_text:
            return node.output_text
        if "output_text" in node.config:
            return str(node.config["output_text"])
        if "value" in node.config:
            return str(node.config["value"])
        return node.label
