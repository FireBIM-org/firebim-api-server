from __future__ import annotations

import time
from collections import Counter, deque
from typing import Any

from rdflib import Graph, Namespace

from app.checks.compiler import RuleCompiler
from app.checks.derived import DerivedOverlay, DeriveEvaluator, resolve_path_values
from app.checks.execution_state import ExecutionContext
from app.checks.pathways import PathwayResolver
from app.checks.planner import RuleExecutionPlanner
from app.checks.result_models import CompiledRuleSet, DecisionNode, EndNode, GraphRef, RunOptions, TargetRef
from app.checks.targeting import TargetResolver
from app.rules.paths import expand_property_path, is_ref_value, ref_path

FB = Namespace("http://example.org/firebim/rule/")
EX = Namespace("http://example.org/firebim/")


class BlindShaclRuleRunner:
    """
    Experimental fast-path runner.

    Strategy:
    1. Parse building graph once
    2. Compile rule TTL once
    3. For each decision node:
       - execute the raw sh:construct query directly on the data graph
       - collect ?subject fb:ruleResult fb:True|fb:False|fb:Unknown
    4. Traverse only on constructed results
    5. Return terminal end-output only

    Important characteristics:
    - no pySHACL validate()
    - no validation report parsing
    - no prechecks
    - no explain trace by default
    - optimized for speed and "blind execution" of the rule TTL itself

    Experimental limitation:
    - if traversal moves from one target class to another (e.g. Building -> Room),
      this runner resolves the next node's own candidate targets independently.
      It does not preserve cross-entity relational linkage between the old and new target.
    """

    def __init__(self) -> None:
        self.derive_evaluator = DeriveEvaluator()
        self.compiler = RuleCompiler()
        self.target_resolver = TargetResolver()
        self.pathway_resolver = PathwayResolver(cache=self.target_resolver.cache)
        self.planner = RuleExecutionPlanner()

    def run_rule(
        self,
        *,
        graph_id: str,
        graph_uri: str,
        graph_ttl: str,
        rule_id: str,
        rule_ttl: str,
        include_targets: bool = True,
        include_debug: bool = False,
        max_steps: int = 100000,
    ) -> dict[str, Any]:
        started = time.perf_counter()

        compiled = self.compiler.compile_from_ttl(rule_ttl)
        graph_ref = GraphRef(graph_id=graph_id, graph_uri=graph_uri, ttl=graph_ttl)
        data_graph = self._parse_graph(graph_ttl)
        graph_index = self.target_resolver.get_graph_index(graph_ref, graph=data_graph)
        execution_context = ExecutionContext(
            graph_ref=graph_ref,
            compiled=compiled,
            options=RunOptions(),
            graph_cache_key=graph_index.graph_cache_key,
            graph_index=graph_index,
            schema=graph_index.schema,
            plan=self.planner.plan(compiled=compiled, schema=graph_index.schema, options=RunOptions()),
        )

        overlay = DerivedOverlay(rule_id=rule_id, graph_id=graph_id)
        derived_props = {
            str(node.property_path)
            for node in compiled.nodes.values()
            if node.node_type == "derive" and node.property_path
        }
        lazy_nodes = {
            node.node_id
            for node in compiled.nodes.values()
            if node.node_type == "decision" and self._reads_any(node, derived_props)
        }
        decision_results = self._precompute_decision_results(
            data_graph=data_graph,
            compiled=compiled,
            skip_node_ids=lazy_nodes,
        )

        entry_decision = self._resolve_first_decision(compiled)
        entry_targets = self.target_resolver.resolve_targets_for_decision(
            graph_ref=graph_ref,
            node=entry_decision,
            graph_index=graph_index,
        )

        terminal_hits = self._traverse_to_terminal_outputs(
            compiled=compiled,
            graph_ref=graph_ref,
            decision_results=decision_results,
            execution_context=execution_context,
            initial_node_id=entry_decision.node_id,
            initial_targets=[item.target_node for item in entry_targets],
            max_steps=max_steps,
            data_graph=data_graph,
            overlay=overlay,
            lazy_nodes=lazy_nodes,
        )

        terminal_counts = Counter(hit["terminal_node_id"] for hit in terminal_hits)
        message_counts = Counter(hit["terminal_value"] for hit in terminal_hits)

        passed = 0
        failed = 0
        unknown = 0

        for hit in terminal_hits:
            status = hit["final_status"]
            if status == "True":
                passed += 1
            elif status == "False":
                failed += 1
            else:
                unknown += 1

        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)

        return {
            "success": True,
            "error": None,
            "message": None,
            "fallback": False,
            "mode": "blind_shacl_construct",
            "rule_id": rule_id,
            "graph_id": graph_id,
            "summary": {
                "total_targets": len(terminal_hits),
                "passed": passed,
                "failed": failed,
                "unknown": unknown,
                "conforms": failed == 0 and unknown == 0,
            },
            "end_outputs": terminal_hits if include_targets else [],
            "derived_values": list(overlay.records),
            "terminal_counts": dict(terminal_counts),
            "message_counts": dict(message_counts),
            "debug": {
                "elapsed_ms": elapsed_ms,
                "decision_node_count": sum(1 for node in compiled.nodes.values() if node.node_type == "decision"),
                "derive_node_count": sum(1 for node in compiled.nodes.values() if node.node_type == "derive"),
                "lazy_decision_nodes": sorted(lazy_nodes),
                "end_node_count": sum(1 for node in compiled.nodes.values() if node.node_type == "end"),
                "decision_result_sizes": {node_id: len(result_map) for node_id, result_map in decision_results.items()},
                "compiled_rule_id": compiled.rule_id,
                "compiled_entry_node_id": compiled.entry_node_id,
                "experimental_mode": True,
            }
            if include_debug
            else {},
        }

    def _parse_graph(self, ttl: str) -> Graph:
        graph = Graph()
        graph.parse(data=ttl, format="turtle")
        return graph

    def _resolve_first_decision(self, compiled: CompiledRuleSet) -> DecisionNode:
        entry = compiled.get_node(compiled.entry_node_id)
        if entry.node_type != "start":
            raise ValueError(f"Compiled rule entry '{compiled.entry_node_id}' is not a start node")
        if not entry.next_node_id:
            raise ValueError(f"Start node '{entry.node_id}' has no next_node_id")

        next_node = compiled.get_node(entry.next_node_id)
        if next_node.node_type not in ("decision", "derive"):
            raise ValueError(f"First executable node '{next_node.node_id}' is not a decision or derive node")
        return next_node

    @staticmethod
    def _reads_any(node: DecisionNode, props: set[str]) -> bool:
        if not props:
            return False
        segments = set(expand_property_path(node.property_path))
        if is_ref_value(node.value):
            segments |= set(expand_property_path(ref_path(node.value)))
        return bool(segments & props)

    def _precompute_decision_results(
        self,
        *,
        data_graph: Graph,
        compiled: CompiledRuleSet,
        skip_node_ids: set[str] | None = None,
    ) -> dict[str, dict[str, str]]:
        """
        Returns:
            {
              node_id: {
                target_uri: "True" | "False" | "Unknown"
              }
            }
        """
        result: dict[str, dict[str, str]] = {}

        for node in compiled.nodes.values():
            if node.node_type != "decision":
                continue
            if skip_node_ids and node.node_id in skip_node_ids:
                continue

            result[node.node_id] = self._execute_construct_for_node(
                data_graph=data_graph,
                node=node,
            )

        return result

    def _execute_construct_for_node(
        self,
        *,
        data_graph: Graph,
        node: DecisionNode,
    ) -> dict[str, str]:
        if not node.sparql_construct or not str(node.sparql_construct).strip():
            return {}

        query_text = str(node.sparql_construct)
        if "PREFIX fb:" not in query_text:
            query_text = f"PREFIX fb: <{FB}>\nPREFIX ex: <http://example.org/firebim/>\n" + query_text
        constructed = data_graph.query(query_text)
        constructed_graph = Graph()

        if isinstance(constructed, Graph):
            constructed_graph = constructed
        else:
            try:
                for triple in constructed:
                    if len(triple) == 3:
                        constructed_graph.add(triple)
            except Exception:
                return {}

        statuses: dict[str, str] = {}

        for subject, _, obj in constructed_graph.triples((None, FB.ruleResult, None)):
            statuses[str(subject)] = self._status_from_result_iri(str(obj))

        return statuses

    def _status_from_result_iri(self, iri: str) -> str:
        if iri.endswith("True"):
            return "True"
        if iri.endswith("False"):
            return "False"
        if iri.endswith("Unknown"):
            return "Unknown"
        return "Unknown"

    def _traverse_to_terminal_outputs(
        self,
        *,
        compiled: CompiledRuleSet,
        graph_ref: GraphRef,
        decision_results: dict[str, dict[str, str]],
        execution_context: ExecutionContext,
        initial_node_id: str,
        initial_targets: list[str],
        max_steps: int,
        data_graph: Graph | None = None,
        overlay: DerivedOverlay | None = None,
        lazy_nodes: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        lazy_nodes = lazy_nodes or set()
        queue = deque((initial_node_id, target, 0) for target in initial_targets)
        seen: set[tuple[str, str]] = set()
        terminal_hits: list[dict[str, Any]] = []
        steps = 0

        while queue:
            node_id, target, depth = queue.popleft()
            steps += 1
            if steps > max_steps:
                break

            state_key = (node_id, target)
            if state_key in seen:
                continue
            seen.add(state_key)

            node = compiled.get_node(node_id)

            if node.node_type == "end":
                terminal_hits.append(
                    {
                        "target_node": target,
                        "terminal_node_id": node.node_id,
                        "terminal_value": self._end_value(node),
                        "final_status": self._status_from_end_node(node),
                    }
                )
                continue

            if node.node_type == "derive" and overlay is not None and data_graph is not None:
                status, _reason, _message, _evidence = self.derive_evaluator.run(
                    graph=data_graph,
                    overlay=overlay,
                    target=target,
                    node_id=node.node_id,
                    target_class=node.target_class,
                    property_path=str(node.property_path),
                    expression=str(node.expression),
                    prefixes=node.prefixes,
                )
                outcome = status
            elif node.node_type != "decision":
                continue
            elif node.node_id in lazy_nodes and data_graph is not None:
                outcome = self._evaluate_lazily(node=node, target=target, data_graph=data_graph, overlay=overlay)
            else:
                node_result_map = decision_results.get(node.node_id, {})
                outcome = node_result_map.get(target, "Unknown")

            next_node_id = self._branch_next_node_id(node=node, outcome=outcome)
            if not next_node_id:
                terminal_hits.append(
                    {
                        "target_node": target,
                        "terminal_node_id": None,
                        "terminal_value": f"Missing branch target for outcome {outcome}",
                        "final_status": "Unknown",
                    }
                )
                continue

            next_node = compiled.get_node(next_node_id)

            if next_node.node_type == "end":
                terminal_hits.append(
                    {
                        "target_node": target,
                        "terminal_node_id": next_node.node_id,
                        "terminal_value": self._end_value(next_node),
                        "final_status": self._status_from_end_node(next_node),
                    }
                )
                continue

            if next_node.node_type not in ("decision", "derive"):
                continue

            if next_node.target_class == node.target_class:
                queue.append((next_node.node_id, target, depth + 1))
                continue

            # Experimental cross-class jump:
            # resolve the next node's own candidate targets independently.
            resolved_batch = self.pathway_resolver.resolve_forward(
                current_targets=[TargetRef(target_node=target, target_class=node.target_class)],
                from_node=node,
                to_node=next_node,
                execution_context=execution_context,
            )
            for next_target in resolved_batch.targets:
                queue.append((next_node.node_id, next_target.target_node, depth + 1))

        return terminal_hits

    def _evaluate_lazily(
        self, *, node: DecisionNode, target: str, data_graph: Graph, overlay: DerivedOverlay | None
    ) -> str:
        """Direct evaluation against base graph + overlay for decisions that read derived properties."""
        from app.checks.evaluator_direct import DirectAtomicEvaluator

        evaluator = DirectAtomicEvaluator()
        segments = expand_property_path(node.property_path)
        values = resolve_path_values(data_graph, target, segments, overlay)
        if len(values) != 1:
            return "Unknown"
        expected = node.value
        if is_ref_value(expected):
            ref_values = resolve_path_values(data_graph, target, expand_property_path(ref_path(expected)), overlay)
            if len(ref_values) != 1:
                return "Unknown"
            expected = evaluator.term_to_python(ref_values[0])
        try:
            ok = evaluator._compare(
                actual=evaluator.term_to_python(values[0]), operator=node.operator or "", expected=expected
            )
        except Exception:
            return "Unknown"
        return "True" if ok else "False"

    def _branch_next_node_id(self, *, node: DecisionNode, outcome: str) -> str | None:
        if outcome == "True":
            return node.true_node_id
        if outcome == "False":
            return node.false_node_id
        return node.unknown_node_id

    def _end_value(self, node: EndNode) -> str:
        if node.output_text:
            return node.output_text
        if "output_text" in node.config:
            return str(node.config["output_text"])
        if "value" in node.config:
            return str(node.config["value"])
        return node.label or node.node_id

    def _status_from_end_node(self, node: EndNode) -> str:
        end_kind = str(node.end_kind or node.config.get("end_kind") or "").strip().lower()
        # same vocabulary as CheckRuntime._infer_end_status
        if end_kind in {"pass", "passed", "true", "success"}:
            return "True"
        if end_kind in {"fail", "failed", "false", "error"}:
            return "False"
        if end_kind in {"unknown", "missing_data", "missing-data"}:
            return "Unknown"

        text = self._end_value(node).strip().lower()

        if "missing data" in text or "cannot be evaluated" in text or "unknown" in text:
            return "Unknown"

        if "did not pass" in text or "failed" in text or "not pass" in text:
            return "False"

        if "passed" in text or text == "pass":
            return "True"

        return "Unknown"
