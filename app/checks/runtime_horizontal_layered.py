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
from app.checks.runtime import CheckRuntime, _TraversalState


class HorizontalLayeredCheckRuntime(CheckRuntime):
    """
    Batch-oriented runtime that walks the rule graph in horizontal layers.

    Analogy:
    - all active states standing on the same decision row are evaluated together
    - the solver then fans them forward to the next row of active states
    """

    engine_mode = "horizontal_compiled_runtime"
    batch_strategy = "horizontal_rows"
    evaluation_shape = "horizontal_row"

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
        self._new_overlay(graph_ref, compiled)

        started_targets = perf_counter()
        initial_targets = self.target_resolver.resolve_targets_for_decision(
            graph_ref=graph_ref,
            node=first_decision,
            graph_index=execution_context.graph_index,
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
            )
            for target in initial_targets
        ]
        traces: list[TargetTrace] = []

        while active_states:
            grouped = self._group_states_by_node_row(active_states)
            next_states: list[_TraversalState] = []
            for node_id, row_states in grouped.items():
                execution_context.metrics.record_active_targets(node_id, len(row_states))
                row_traces, row_next_states = self._process_node_row(
                    graph_ref=graph_ref,
                    compiled=compiled,
                    node_id=node_id,
                    states=row_states,
                    options=options,
                    metrics=metrics,
                )
                traces.extend(row_traces)
                next_states.extend(row_next_states)
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
        return self._attach_derived(result, options)

    def _group_states_by_node_row(self, states: list[_TraversalState]) -> OrderedDict[str, list[_TraversalState]]:
        grouped: OrderedDict[str, list[_TraversalState]] = OrderedDict()
        for state in states:
            grouped.setdefault(state.node_id, []).append(state)
        return grouped

    def _process_node_row(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        node_id: str,
        states: list[_TraversalState],
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ) -> tuple[list[TargetTrace], list[_TraversalState]]:
        traces: list[TargetTrace] = []
        next_states: list[_TraversalState] = []

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
                            f"Traversal stopped for target '{state.target_node}' because "
                            f"max_steps_per_target={options.max_steps_per_target} was reached"
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
                continue

            if node.node_type not in self.EXECUTABLE_TYPES:
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

        if not executable_states or node.node_type not in self.EXECUTABLE_TYPES:
            return traces, next_states

        if self._active_execution_context is not None:
            self._active_execution_context.metrics.record_node_visit(
                node.node_id,
                node_type=node.node_type,
                target_class=node.target_class,
                property_path=node.property_path,
                sample_target=executable_states[0].target_node if executable_states else None,
            )

        outcomes = self._evaluate_decision_row(
            graph_ref=graph_ref,
            compiled=compiled,
            decision=node,
            states=executable_states,
            options=options,
            metrics=metrics,
        )

        for state, outcome in zip(executable_states, outcomes, strict=True):
            next_node_id = self._branch_next_node_id(node, outcome.status)
            step = TargetTraceStep(
                node_id=node.node_id,
                node_type=node.node_type,
                status=outcome.status,
                next_node_id=next_node_id,
                evidence=self._step_evidence(outcome),
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

    def _evaluate_decision_row(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        decision: DecisionNode,
        states: list[_TraversalState],
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ) -> list[AtomicOutcome]:
        if decision.node_type == "derive":
            # derive elements write per target; never batched, never cached
            started_derive = perf_counter()
            outcomes = [
                self._execute_derive(graph_ref=graph_ref, node=decision, target=state.target_node, metrics=metrics)
                for state in states
            ]
            accumulate_timing(metrics, "evaluate_derive_total", perf_counter() - started_derive)
            return outcomes
        self._prefetch_row_property_values(
            graph_ref=graph_ref,
            decision=decision,
            states=states,
            metrics=metrics,
        )
        started = perf_counter()
        outcomes = self._evaluate_decision_row_fast_path(
            graph_ref=graph_ref,
            compiled=compiled,
            decision=decision,
            states=states,
            options=options,
            metrics=metrics,
        )
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

    def _evaluate_decision_row_fast_path(
        self,
        *,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        decision: DecisionNode,
        states: list[_TraversalState],
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ) -> list[AtomicOutcome]:
        row_extra = {
            "include_report_text": options.include_report_text,
            "include_report_graph_ttl": options.include_report_graph_ttl,
            "metrics": metrics or {},
            "evaluation_shape": self.evaluation_shape,
            "row_shape": "horizontal",
            "row_node_id": decision.node_id,
            "row_size": len(states),
            "graph_index": self._active_execution_context.graph_index if self._active_execution_context else None,
            "execution_context": self._active_execution_context,
            "overlay": self._active_overlay,
        }
        if isinstance(self.evaluator, HybridAtomicEvaluator) and self.evaluator.direct_evaluator.supports(decision):
            increment_counter(metrics, "horizontal_row_direct_batches")
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
            return [deepcopy(outcome_map[state.target_node]) for state in states]

        increment_counter(metrics, "horizontal_row_legacy_fallbacks")
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
        return outcomes

    def _prefetch_row_property_values(
        self,
        *,
        graph_ref: GraphRef,
        decision: DecisionNode,
        states: list[_TraversalState],
        metrics: dict[str, Any] | None = None,
    ) -> None:
        if not decision.property_path or not self._active_execution_context:
            return
        if not isinstance(self.evaluator, HybridAtomicEvaluator):
            return
        increment_counter(metrics, "property_prefetch_count")
        targets = [state.target_node for state in states]
        self.evaluator.direct_evaluator.prefetch_property_values(
            graph_ref=graph_ref,
            targets=targets,
            property_path=decision.property_path,
            metrics=metrics,
            overlay=self._active_overlay,
        )
