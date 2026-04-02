from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass

from app.checks.evaluator_base import AtomicEvaluator
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


class CheckRuntime:
    """
    Deterministic rule traversal runtime.

    Supports both:
    - RuleSet entryPoint -> start node
    - RuleSet entryPoint -> decision node
    """

    def __init__(
        self,
        *,
        evaluator: AtomicEvaluator,
        target_resolver: TargetResolver | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.target_resolver = target_resolver or TargetResolver()

    def run_rule(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        options: RunOptions | None = None,
    ) -> RuleRunResult:
        options = options or RunOptions()

        first_decision = self._resolve_entry_decision(compiled)

        initial_targets = self.target_resolver.resolve_targets_for_decision(
            graph_ref=graph_ref,
            node=first_decision,
        )

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
                )
            )

        summary = self._build_summary(traces)

        result = RuleRunResult(
            rule_id=compiled.rule_id,
            graph_id=graph_ref.graph_id,
            summary=summary,
            targets=traces if options.include_targets else [],
            compiled_rule={
                "rule_id": compiled.rule_id,
                "entry_node_id": compiled.entry_node_id,
                "node_count": len(compiled.nodes),
                "metadata": compiled.metadata,
            },
            engine={
                "runtime": self.__class__.__name__,
                "evaluator": self.evaluator.__class__.__name__,
                "target_resolver": self.target_resolver.__class__.__name__,
                "mode": "compiled_runtime",
            },
            debug={
                "initial_target_count": len(initial_targets),
                "initial_target_node_id": first_decision.node_id,
                "include_trace": options.include_trace,
                "include_targets": options.include_targets,
                "max_steps_per_target": options.max_steps_per_target,
            },
        )

        if not options.include_report_text:
            result.report_text = None
        if not options.include_report_graph_ttl:
            result.report_graph_ttl = None

        return result

    def _resolve_entry_decision(self, compiled: CompiledRuleSet) -> DecisionNode:
        entry_node = compiled.get_node(compiled.entry_node_id)

        if entry_node.node_type == "decision":
            return entry_node

        if entry_node.node_type == "start":
            return self._resolve_first_decision_from_start(compiled, entry_node)

        raise ValueError(
            f"Compiled rule '{compiled.rule_id}' entry node "
            f"'{compiled.entry_node_id}' must be a start or decision node, "
            f"got '{entry_node.node_type}'"
        )

    def _resolve_first_decision_from_start(
        self,
        compiled: CompiledRuleSet,
        start_node: StartNode,
    ) -> DecisionNode:
        if not start_node.next_node_id:
            raise ValueError(f"Start node '{start_node.node_id}' has no next_node_id")

        next_node = compiled.get_node(start_node.next_node_id)
        if next_node.node_type != "decision":
            raise ValueError(f"First executable node '{next_node.node_id}' must be a decision node")
        return next_node

    def _traverse_from_target(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        initial_node_id: str,
        target_node: str,
        target_class: str | None,
        options: RunOptions,
    ) -> list[TargetTrace]:
        initial_state = _TraversalState(
            node_id=initial_node_id,
            target_node=target_node,
            target_class=target_class,
            steps=[],
            visited_node_ids=[],
            depth=0,
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
                            f"Traversal stopped for target '{state.target_node}' "
                            f"because max_steps_per_target={options.max_steps_per_target} was reached"
                        ),
                        steps=state.steps,
                        evidence={
                            "reason_code": "max_steps_reached",
                            "visited_node_ids": state.visited_node_ids,
                        },
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
                    evidence={
                        "end_kind": node.end_kind,
                        "output_text": node.output_text,
                    },
                )

                traces.append(
                    self._make_terminal_trace(
                        target_node=state.target_node,
                        target_class=state.target_class,
                        final_status=terminal_status,
                        terminal_node_id=node.node_id,
                        message=self._end_message(node),
                        steps=state.steps + [end_step],
                        evidence={
                            "end_kind": node.end_kind,
                            "output_text": node.output_text,
                        },
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
                        message=(
                            f"Unsupported executable node type '{node.node_type}' encountered at '{node.node_id}'"
                        ),
                        steps=state.steps,
                        evidence={"reason_code": "unsupported_node_type"},
                        include_trace=options.include_trace,
                    )
                )
                if options.fail_fast:
                    break
                continue

            decision = node
            ctx = EvalContext(
                rule_id=compiled.rule_id,
                graph_id=graph_ref.graph_id,
                graph_uri=graph_ref.graph_uri,
                inference=options.inference,
                advanced=options.advanced,
                debug=options.debug,
                current_step_index=len(state.steps),
                visited_node_ids=state.visited_node_ids + [decision.node_id],
                extra={},
            )

            outcome = self.evaluator.evaluate(
                graph_ref=graph_ref,
                node=decision,
                target=state.target_node,
                ctx=ctx,
            )

            next_node_id = self._branch_next_node_id(decision, outcome.status)

            step_evidence = deepcopy(outcome.evidence)
            if outcome.reason_code:
                step_evidence["reason_code"] = outcome.reason_code
            if outcome.message:
                step_evidence["message"] = outcome.message

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
            )

            if not next_states:
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
        next_node,
        current_steps: list[TargetTraceStep],
        visited_node_ids: list[str],
        depth: int,
    ) -> list[_TraversalState]:
        if next_node.node_type == "end":
            return [
                _TraversalState(
                    node_id=next_node.node_id,
                    target_node=current_target_node,
                    target_class=current_target_class,
                    steps=current_steps,
                    visited_node_ids=visited_node_ids,
                    depth=depth,
                )
            ]

        if next_node.node_type != "decision":
            return [
                _TraversalState(
                    node_id=next_node.node_id,
                    target_node=current_target_node,
                    target_class=current_target_class,
                    steps=current_steps,
                    visited_node_ids=visited_node_ids,
                    depth=depth,
                )
            ]

        if next_node.target_class == current_target_class:
            return [
                _TraversalState(
                    node_id=next_node.node_id,
                    target_node=current_target_node,
                    target_class=current_target_class,
                    steps=current_steps,
                    visited_node_ids=visited_node_ids,
                    depth=depth,
                )
            ]

        resolved_targets = self.target_resolver.resolve_targets_for_decision(
            graph_ref=graph_ref,
            node=next_node,
        )

        return [
            _TraversalState(
                node_id=next_node.node_id,
                target_node=item.target_node,
                target_class=item.target_class,
                steps=current_steps,
                visited_node_ids=visited_node_ids,
                depth=depth,
            )
            for item in resolved_targets
        ]

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
        return TargetTrace(
            target_node=target_node,
            target_class=target_class,
            final_status=final_status,
            terminal_node_id=terminal_node_id,
            message=message,
            trace=steps if include_trace else [],
            evidence=evidence,
        )

    def _last_status(self, steps: list[TargetTraceStep]) -> CheckStatus:
        if not steps:
            return "Unknown"
        return steps[-1].status

    def _infer_end_status(self, node: EndNode, incoming_status: CheckStatus) -> CheckStatus:
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
