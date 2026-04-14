from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from time import perf_counter
from typing import Any

from app.checks.caching import accumulate_timing, increment_counter
from app.checks.evaluator_hybrid import HybridAtomicEvaluator
from app.checks.result_models import (
    AtomicOutcome,
    CompiledRuleSet,
    DecisionNode,
    EvalContext,
    GraphRef,
    RuleRunResult,
    RunOptions,
    TargetTrace,
    TargetTraceStep,
)
from app.checks.runtime import _TraversalState
from app.checks.runtime_horizontal_layered import HorizontalLayeredCheckRuntime


class VerticalPathwayCheckRuntime(HorizontalLayeredCheckRuntime):
    """
    Batch-oriented runtime that walks the rule graph in vertical pathway segments.

    Analogy:
    - a horizontal solver evaluates one row of similar facts at a time
    - a vertical solver picks one linear pathway column and moves a cohort of targets
      through that column before returning to the next cohort

    First implementation strategy:
    - preserve the legacy semantics
    - only collapse pathway segments when the control flow is linear for all outcomes
    - keep the same fallback behavior as the horizontal solver for anything more complex
    """

    engine_mode = "vertical_compiled_runtime"
    batch_strategy = "vertical_columns"
    evaluation_shape = "vertical_pathway"

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
        if execution_context.plan.strategy == "backward":
            initial_targets = self._filter_initial_targets_backward(
                graph_ref=graph_ref,
                compiled=compiled,
                initial_targets=initial_targets,
                options=options,
                metrics=metrics,
            )
        accumulate_timing(metrics, "resolve_targets_total", perf_counter() - started_targets)
        execution_context.metrics.record_active_targets(first_decision.node_id, len(initial_targets))

        active_states: list[_TraversalState] = [
            _TraversalState(
                node_id=first_decision.node_id,
                target_node=target.target_node,
                target_class=target.target_class,
                steps=[],
                visited_node_ids=[],
                depth=0,
                binding_frame=self._make_initial_binding(first_decision.node_id, target.target_node),
            )
            for target in initial_targets
        ]
        traces: list[TargetTrace] = []

        while active_states:
            grouped = self._group_states_by_vertical_pathway(active_states, compiled)
            next_states: list[_TraversalState] = []
            for pathway_signature, pathway_states in grouped.items():
                if pathway_signature:
                    execution_context.metrics.record_active_targets(pathway_signature[0], len(pathway_states))
                column_traces, column_next_states = self._process_vertical_pathway(
                    graph_ref=graph_ref,
                    compiled=compiled,
                    pathway_signature=pathway_signature,
                    states=pathway_states,
                    options=options,
                    metrics=metrics,
                )
                traces.extend(column_traces)
                next_states.extend(column_next_states)
            active_states = next_states

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
                "mode": self.engine_mode,
                "batch_strategy": self.batch_strategy,
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

    def _group_states_by_vertical_pathway(
        self,
        states: list[_TraversalState],
        compiled: CompiledRuleSet,
    ) -> OrderedDict[tuple[str, ...], list[_TraversalState]]:
        grouped: OrderedDict[tuple[str, ...], list[_TraversalState]] = OrderedDict()
        for state in states:
            signature = self._build_vertical_pathway_signature(state.node_id, compiled)
            grouped.setdefault(signature, []).append(state)
        return grouped

    def _build_vertical_pathway_signature(self, start_node_id: str, compiled: CompiledRuleSet) -> tuple[str, ...]:
        signature: list[str] = []
        seen: set[str] = set()
        current_node_id: str | None = start_node_id
        max_segment = 32

        while current_node_id and current_node_id not in seen and len(signature) < max_segment:
            node = compiled.get_node(current_node_id)
            signature.append(current_node_id)
            seen.add(current_node_id)

            if node.node_type != "decision":
                break
            if not self._is_linear_vertical_step(node):
                break

            next_node_id = self._linear_next_node_id(node)
            if not next_node_id:
                break
            current_node_id = next_node_id

        return tuple(signature)

    def _is_linear_vertical_step(self, node: DecisionNode) -> bool:
        return bool(node.true_node_id and node.true_node_id == node.false_node_id == node.unknown_node_id)

    def _linear_next_node_id(self, node: DecisionNode) -> str | None:
        if self._is_linear_vertical_step(node):
            return node.true_node_id
        return None

    def _process_vertical_pathway(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        pathway_signature: tuple[str, ...],
        states: list[_TraversalState],
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ) -> tuple[list[TargetTrace], list[_TraversalState]]:
        traces: list[TargetTrace] = []
        active_states = list(states)

        for node_id in pathway_signature:
            if not active_states:
                break
            grouped = self._group_states_by_node_row(active_states)
            row_states = grouped.get(node_id, [])
            carry_states = [state for state in active_states if state.node_id != node_id]
            row_traces, row_next_states = self._process_vertical_node_row(
                graph_ref=graph_ref,
                compiled=compiled,
                node_id=node_id,
                states=row_states,
                pathway_signature=pathway_signature,
                options=options,
                metrics=metrics,
            )
            traces.extend(row_traces)
            active_states = carry_states + row_next_states

        return traces, active_states

    def _process_vertical_node_row(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        node_id: str,
        states: list[_TraversalState],
        pathway_signature: tuple[str, ...],
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ) -> tuple[list[TargetTrace], list[_TraversalState]]:
        traces: list[TargetTrace] = []
        next_states: list[_TraversalState] = []
        if not states:
            return traces, next_states

        executable_states: list[_TraversalState] = []
        node = compiled.get_node(node_id)

        for state in states:
            if state.depth >= options.max_steps_per_target:
                traces.append(
                    self._make_terminal_trace(
                        target_node=state.target_node,
                        target_class=state.target_class,
                        final_status="Unknown",
                        terminal_node_id=None,
                        message=(
                            f"Traversal stopped for target '{state.target_node}' because max_steps_per_target={options.max_steps_per_target} was reached"
                        ),
                        steps=state.steps,
                        evidence={"reason_code": "max_steps_reached", "visited_node_ids": state.visited_node_ids},
                        include_trace=options.include_trace,
                    )
                )
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
                continue

            if node.node_type == "end":
                terminal_status = self._infer_end_status(node, incoming_status=self._last_status(state.steps))
                end_step = TargetTraceStep(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    status=terminal_status,
                    next_node_id=None,
                    evidence={
                        "end_kind": getattr(node, "end_kind", None),
                        "output_text": getattr(node, "output_text", None),
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
                        evidence={"reason_code": "end_node_reached", "end_kind": getattr(node, "end_kind", None)},
                        include_trace=options.include_trace,
                    )
                )
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
                continue

            executable_states.append(state)

        if not executable_states or node.node_type != "decision":
            return traces, next_states

        if self._active_execution_context is not None:
            self._active_execution_context.metrics.record_node_visit(
                node.node_id,
                node_type=node.node_type,
                target_class=node.target_class,
                property_path=node.property_path,
                sample_target=executable_states[0].target_node if executable_states else None,
            )

        outcomes = self._evaluate_vertical_node_row(
            graph_ref=graph_ref,
            compiled=compiled,
            decision=node,
            states=executable_states,
            pathway_signature=pathway_signature,
            options=options,
            metrics=metrics,
        )

        for state, outcome in zip(executable_states, outcomes):
            next_node_id = self._branch_next_node_id(node, outcome.status)
            step = TargetTraceStep(
                node_id=node.node_id,
                node_type=node.node_type,
                status=outcome.status,
                next_node_id=next_node_id,
                evidence=outcome.evidence,
            )
            updated_steps = state.steps + [step]
            visited = state.visited_node_ids + [state.node_id]

            if not next_node_id:
                traces.append(
                    self._make_terminal_trace(
                        target_node=state.target_node,
                        target_class=state.target_class,
                        final_status=outcome.status,
                        terminal_node_id=node.node_id,
                        message=outcome.message,
                        steps=updated_steps,
                        evidence={
                            "reason_code": outcome.reason_code or "missing_branch",
                            **outcome.evidence,
                        },
                        include_trace=options.include_trace,
                    )
                )
                continue

            next_node = compiled.get_node(next_node_id)
            next_states.extend(
                self._resolve_next_states(
                    graph_ref=graph_ref,
                    current_target_node=state.target_node,
                    current_target_class=state.target_class,
                    current_node=node,
                    next_node=next_node,
                    current_steps=updated_steps,
                    visited_node_ids=visited,
                    depth=state.depth + 1,
                    binding_frame=state.binding_frame.with_binding(node.node_id, state.target_node),
                    metrics=metrics,
                )
            )

        return traces, next_states

    def _evaluate_vertical_node_row(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        decision: DecisionNode,
        states: list[_TraversalState],
        pathway_signature: tuple[str, ...],
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ) -> list[AtomicOutcome]:
        self._prefetch_row_property_values(
            graph_ref=graph_ref,
            decision=decision,
            states=states,
            metrics=metrics,
        )
        started = perf_counter()
        row_extra = {
            "include_report_text": options.include_report_text,
            "include_report_graph_ttl": options.include_report_graph_ttl,
            "metrics": metrics or {},
            "evaluation_shape": self.evaluation_shape,
            "column_shape": "vertical",
            "column_entry_node_id": pathway_signature[0] if pathway_signature else decision.node_id,
            "column_node_id": decision.node_id,
            "column_length": len(pathway_signature),
            "column_size": len(states),
            "graph_index": self._active_execution_context.graph_index if self._active_execution_context else None,
            "execution_context": self._active_execution_context,
        }
        if isinstance(self.evaluator, HybridAtomicEvaluator) and self.evaluator.direct_evaluator.supports(decision):
            increment_counter(metrics, "vertical_column_direct_batches")
            targets = [state.target_node for state in states]
            row_ctx = EvalContext(
                rule_id=compiled.rule_id,
                graph_id=graph_ref.graph_id,
                graph_uri=graph_ref.graph_uri,
                inference=options.inference,
                advanced=options.advanced,
                debug=options.debug,
                current_step_index=min((state.depth for state in states), default=0),
                visited_node_ids=[],
                extra=row_extra,
            )
            outcome_map = self.evaluator.direct_evaluator.evaluate_many(
                graph_ref=graph_ref,
                node=decision,
                targets=targets,
                ctx=row_ctx,
            )
            counters = row_ctx.extra.setdefault("metrics", {}).setdefault("counters", {})
            counters["direct_evaluator_attempts"] = int(counters.get("direct_evaluator_attempts", 0)) + len(targets)
            total_seconds = perf_counter() - started
            total_ms = round(total_seconds * 1000.0, 3)
            accumulate_timing(metrics, "evaluate_atomic_total", total_seconds)
            if self._active_execution_context is not None:
                self._active_execution_context.metrics.record_node_timing(
                    decision.node_id,
                    duration_ms=total_ms,
                    target_class=decision.target_class,
                    target_count=len(states),
                )
            outcomes = [deepcopy(outcome_map[state.target_node]) for state in states]
            if options.diagnostics and states:
                per_target_ms = round(total_ms / max(len(states), 1), 3)
                for outcome in outcomes:
                    outcome.evidence = {**(outcome.evidence or {}), "duration_ms": per_target_ms}
            return outcomes

        increment_counter(metrics, "vertical_column_legacy_fallbacks")
        outcomes: list[AtomicOutcome] = []
        for state in states:
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
                        **row_extra,
                        "binding_frame": state.binding_frame.bindings,
                    },
                ),
            )
            outcomes.append(outcome)
        total_seconds = perf_counter() - started
        total_ms = round(total_seconds * 1000.0, 3)
        accumulate_timing(metrics, "evaluate_atomic_total", total_seconds)
        if self._active_execution_context is not None:
            self._active_execution_context.metrics.record_node_timing(
                decision.node_id,
                duration_ms=total_ms,
                target_class=decision.target_class,
                target_count=len(states),
            )
        if options.diagnostics and states:
            per_target_ms = round(total_ms / max(len(states), 1), 3)
            for outcome in outcomes:
                outcome.evidence = {**(outcome.evidence or {}), "duration_ms": per_target_ms}
        return outcomes

    def _filter_initial_targets_backward(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        initial_targets,
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ):
        execution_context = self._active_execution_context
        if execution_context is None or not execution_context.plan.backward_safe:
            if execution_context is not None:
                execution_context.metrics.warnings.append(
                    "Backward planning requested but not safe; using forward entry targets."
                )
            return initial_targets

        decision_order = execution_context.plan.decision_order
        if not decision_order:
            return initial_targets

        terminal_decision_id = execution_context.plan.backward_terminal_nodes[-1]
        terminal_decision = compiled.get_node(terminal_decision_id)
        if terminal_decision.node_type != "decision":
            return initial_targets

        terminal_targets = self.target_resolver.resolve_targets_for_decision(
            graph_ref=graph_ref,
            node=terminal_decision,
            graph_index=execution_context.graph_index,
            metrics=metrics,
        )
        outcomes = self._evaluate_vertical_node_row(
            graph_ref=graph_ref,
            compiled=compiled,
            decision=terminal_decision,
            states=[
                _TraversalState(
                    node_id=terminal_decision.node_id,
                    target_node=target.target_node,
                    target_class=target.target_class,
                    steps=[],
                    visited_node_ids=[],
                    depth=0,
                    binding_frame=self._make_initial_binding(terminal_decision.node_id, target.target_node),
                )
                for target in terminal_targets
            ],
            pathway_signature=(terminal_decision.node_id,),
            options=options,
            metrics=metrics,
        )
        matching_targets = [target for target, outcome in zip(terminal_targets, outcomes) if outcome.status == "True"]
        increment_counter(metrics, "backward_seed_count", len(matching_targets))
        if not matching_targets:
            execution_context.metrics.warnings.append(
                "Backward planning found no terminal seeds; keeping forward entry targets."
            )
            return initial_targets

        current_targets = matching_targets
        for index in range(len(decision_order) - 2, -1, -1):
            from_node = compiled.get_node(decision_order[index + 1])
            to_node = compiled.get_node(decision_order[index])
            if from_node.node_type != "decision" or to_node.node_type != "decision":
                continue
            current_targets = self.pathway_resolver.resolve_backward(
                current_targets=current_targets,
                from_node=from_node,
                to_node=to_node,
                execution_context=execution_context,
                metrics=metrics,
            ).targets
            if not current_targets:
                execution_context.metrics.warnings.append(
                    f"Backward planning pruned all candidates at transition {from_node.node_id}->{to_node.node_id}; using forward entry targets."
                )
                return initial_targets

        allowed = {target.target_node for target in current_targets}
        filtered_targets = [target for target in initial_targets if target.target_node in allowed]
        return filtered_targets or initial_targets
