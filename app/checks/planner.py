from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.checks.caching import accumulate_timing
from app.checks.graph_schema import GraphDerivedSchema
from app.checks.result_models import CompiledRuleSet, EndNode, RunOptions


@dataclass
class TransitionPlan:
    from_node_id: str
    to_node_id: str
    from_class: str | None
    to_class: str | None
    direction_hint: str
    candidate_predicates: list[str] = field(default_factory=list)
    estimated_from_count: int = 0
    estimated_to_count: int = 0
    estimated_fanout: int = 0


@dataclass
class RuleExecutionPlan:
    decision_order: list[str]
    transitions: dict[tuple[str, str], TransitionPlan]
    predecessor_map: dict[str, list[str]]
    strategy: str
    backward_safe: bool
    backward_terminal_nodes: list[str]
    debug: dict[str, Any] = field(default_factory=dict)


class RuleExecutionPlanner:
    def plan(
        self,
        *,
        compiled: CompiledRuleSet,
        schema: GraphDerivedSchema,
        options: RunOptions,
        metrics: dict[str, Any] | None = None,
    ) -> RuleExecutionPlan:
        started = perf_counter()
        decision_order = self._decision_order(compiled)
        transitions: dict[tuple[str, str], TransitionPlan] = {}
        predecessor_map: dict[str, list[str]] = defaultdict(list)

        for from_node_id, to_node_id in self._decision_edges(compiled):
            from_node = compiled.get_node(from_node_id)
            to_node = compiled.get_node(to_node_id)
            if from_node.node_type != "decision" or to_node.node_type != "decision":
                continue
            predecessor_map[to_node_id].append(from_node_id)
            predicates = schema.candidate_predicates(from_node.target_class, to_node.target_class)
            reverse_predicates = schema.candidate_predicates(from_node.target_class, to_node.target_class, reverse=True)
            if from_node.target_class == to_node.target_class:
                direction_hint = "same_class"
            elif predicates:
                direction_hint = "forward"
            elif reverse_predicates:
                direction_hint = "reverse"
            else:
                direction_hint = "unresolvable"
            edge = schema.class_to_class_edges.get((from_node.target_class or "", to_node.target_class or ""))
            transitions[(from_node_id, to_node_id)] = TransitionPlan(
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                from_class=from_node.target_class,
                to_class=to_node.target_class,
                direction_hint=direction_hint,
                candidate_predicates=predicates or reverse_predicates,
                estimated_from_count=schema.instance_counts_by_class.get(from_node.target_class or "", 0),
                estimated_to_count=schema.instance_counts_by_class.get(to_node.target_class or "", 0),
                estimated_fanout=edge.support if edge is not None else 0,
            )

        backward_terminal_nodes = self._backward_terminal_nodes(compiled)
        has_branching = any(len(preds) > 1 for preds in predecessor_map.values())
        all_linear = all(self._single_decision_successor(compiled, node_id) <= 1 for node_id in decision_order)
        backward_safe = bool(decision_order and backward_terminal_nodes and not has_branching and all_linear)
        strategy = "vertical" if len(decision_order) > 2 else "horizontal"
        if options.planner_strategy == "backward" and backward_safe:
            selected_strategy = "backward"
        elif options.planner_strategy == "backward" or options.planner_strategy == "forward":
            selected_strategy = "forward"
        else:
            selected_strategy = strategy

        debug = {
            "decision_order": decision_order,
            "strategy": selected_strategy,
            "backward_safe": backward_safe,
            "backward_terminal_nodes": backward_terminal_nodes,
        }
        accumulate_timing(metrics, "plan_rule", perf_counter() - started)
        return RuleExecutionPlan(
            decision_order=decision_order,
            transitions=transitions,
            predecessor_map={node_id: list(preds) for node_id, preds in predecessor_map.items()},
            strategy=selected_strategy,
            backward_safe=backward_safe,
            backward_terminal_nodes=backward_terminal_nodes,
            debug=debug,
        )

    def _decision_order(self, compiled: CompiledRuleSet) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        queue: deque[str] = deque([compiled.entry_node_id])
        while queue:
            node_id = queue.popleft()
            if node_id in seen:
                continue
            seen.add(node_id)
            node = compiled.get_node(node_id)
            if node.node_type == "decision":
                ordered.append(node_id)
                for next_id in [node.true_node_id, node.false_node_id, node.unknown_node_id]:
                    if next_id:
                        queue.append(next_id)
            elif node.node_type == "start" and node.next_node_id:
                queue.append(node.next_node_id)
        return ordered

    def _decision_edges(self, compiled: CompiledRuleSet) -> list[tuple[str, str]]:
        edges: list[tuple[str, str]] = []
        for node_id in self._decision_order(compiled):
            node = compiled.get_node(node_id)
            if node.node_type != "decision":
                continue
            for next_id in [node.true_node_id, node.false_node_id, node.unknown_node_id]:
                if not next_id:
                    continue
                next_node = compiled.get_node(next_id)
                if next_node.node_type == "decision":
                    edge = (node_id, next_id)
                    if edge not in edges:
                        edges.append(edge)
        return edges

    def _backward_terminal_nodes(self, compiled: CompiledRuleSet) -> list[str]:
        terminal_nodes: list[str] = []
        for node_id in self._decision_order(compiled):
            node = compiled.get_node(node_id)
            if node.node_type != "decision":
                continue
            for next_id in [node.true_node_id, node.false_node_id, node.unknown_node_id]:
                if not next_id:
                    continue
                next_node = compiled.get_node(next_id)
                if isinstance(next_node, EndNode) and self._end_status(next_node) == "True":
                    terminal_nodes.append(node_id)
                    break
        return terminal_nodes

    def _end_status(self, node: EndNode) -> str:
        end_kind = (node.end_kind or "").strip().lower()
        if end_kind in {"pass", "passed", "true", "success"}:
            return "True"
        if end_kind in {"fail", "failed", "false", "error"}:
            return "False"
        if end_kind in {"unknown", "missing_data", "missing-data"}:
            return "Unknown"
        text = (node.output_text or node.label or "").strip().lower()
        if "pass" in text:
            return "True"
        if "fail" in text:
            return "False"
        return "Unknown"

    def _single_decision_successor(self, compiled: CompiledRuleSet, node_id: str) -> int:
        node = compiled.get_node(node_id)
        if node.node_type != "decision":
            return 0
        successor_ids = set()
        for next_id in [node.true_node_id, node.false_node_id, node.unknown_node_id]:
            if not next_id:
                continue
            next_node = compiled.get_node(next_id)
            if next_node.node_type == "decision":
                successor_ids.add(next_node.node_id)
        return len(successor_ids)
