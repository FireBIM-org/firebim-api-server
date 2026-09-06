from __future__ import annotations

from time import perf_counter
from typing import Any

from rdflib import RDF, Graph, URIRef

from app.checks.caching import CheckExecutionCache, accumulate_timing, get_default_execution_cache, increment_counter
from app.checks.graph_index import GraphIndex, GraphIndexBuilder
from app.checks.result_models import CompiledRuleSet, DecisionNode, GraphRef, TargetRef


def restrict_targets(targets: list[TargetRef], allowed: list[str] | None) -> list[TargetRef]:
    """Keep only the targets listed in `allowed` (RunOptions.target_nodes); no list = no restriction."""
    if not allowed:
        return targets
    wanted = {str(t).strip() for t in allowed if str(t).strip()}
    return [t for t in targets if t.target_node in wanted]


class TargetResolver:
    def __init__(self, cache: CheckExecutionCache | None = None) -> None:
        self.cache = cache or get_default_execution_cache()
        self.index_builder = GraphIndexBuilder(cache=self.cache)

    def resolve_rule_targets(
        self,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> list[TargetRef]:
        graph = self._get_graph(graph_ref, metrics=metrics)
        targets_by_node = self.resolve_targets_by_node(graph_ref, compiled, graph=graph, metrics=metrics)

        merged: dict[str, TargetRef] = {}
        for node_targets in targets_by_node.values():
            for target in node_targets:
                if target.target_node not in merged:
                    merged[target.target_node] = target

        return sorted(merged.values(), key=lambda item: (item.target_class or "", item.target_node))

    def resolve_targets_by_node(
        self,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        graph: Graph | None = None,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, list[TargetRef]]:
        rdf_graph = graph or self._get_graph(graph_ref, metrics=metrics)
        result: dict[str, list[TargetRef]] = {}
        for node in compiled.nodes.values():
            if node.node_type != "decision":
                continue
            result[node.node_id] = self.resolve_targets_for_decision(
                graph_ref=graph_ref,
                node=node,
                graph=rdf_graph,
                metrics=metrics,
            )
        return result

    def resolve_targets_for_decision(
        self,
        graph_ref: GraphRef,
        node: DecisionNode,
        graph: Graph | None = None,
        graph_index: GraphIndex | None = None,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> list[TargetRef]:
        if not node.target_class:
            return []

        index = graph_index or self.get_graph_index(graph_ref, graph=graph, metrics=metrics)
        return index.target_refs_for_class(node.target_class)

    def estimate_target_counts(
        self,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        graph = self._get_graph(graph_ref, metrics=metrics)
        counts: dict[str, int] = {}
        for node in compiled.nodes.values():
            if node.node_type != "decision":
                continue
            if not node.target_class:
                counts[node.node_id] = 0
                continue
            targets = self._resolve_subjects_by_target_class(
                graph_ref=graph_ref,
                graph=graph,
                target_class=node.target_class,
                metrics=metrics,
            )
            counts[node.node_id] = len(targets)
        return counts

    def collect_target_classes(self, compiled: CompiledRuleSet) -> list[str]:
        target_classes = {
            node.target_class for node in compiled.nodes.values() if node.node_type == "decision" and node.target_class
        }
        return sorted(target_classes)

    def _get_graph(self, graph_ref: GraphRef, *, metrics: dict[str, Any] | None = None) -> Graph:
        if not graph_ref.ttl or not graph_ref.ttl.strip():
            raise ValueError(
                f"Graph '{graph_ref.graph_id}' has no TTL content loaded. "
                "TargetResolver currently requires graph_ref.ttl."
            )
        _graph_cache_key, graph = self.cache.get_or_parse_graph(
            graph_uri=graph_ref.graph_uri,
            ttl=graph_ref.ttl,
            metrics=metrics,
        )
        return graph

    def get_graph_index(
        self,
        graph_ref: GraphRef,
        graph: Graph | None = None,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> GraphIndex:
        if graph is not None:
            graph_cache_key, _ = self.cache.get_or_parse_graph(
                graph_uri=graph_ref.graph_uri,
                ttl=graph_ref.ttl or "",
                metrics=metrics,
            )
            cached = self.cache.get_graph_index(graph_cache_key)
            if cached is not None:
                return cached
        return self.index_builder.build(graph_ref=graph_ref, metrics=metrics)

    def _resolve_subjects_by_target_class(
        self,
        graph_ref: GraphRef,
        graph: Graph,
        target_class: str,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> list[str]:
        if not graph_ref.ttl or not graph_ref.ttl.strip():
            raise ValueError(f"Graph '{graph_ref.graph_id}' has no TTL content loaded")
        graph_cache_key, _ = self.cache.get_or_parse_graph(
            graph_uri=graph_ref.graph_uri,
            ttl=graph_ref.ttl,
            metrics=metrics,
        )

        cached = self.cache.get_targets(graph_cache_key, target_class)
        if cached is not None:
            increment_counter(metrics, "target_cache_hits")
            return cached

        increment_counter(metrics, "target_cache_misses")
        started = perf_counter()
        target_uri = URIRef(target_class)
        subjects = sorted({str(subject) for subject in graph.subjects(RDF.type, target_uri)})
        accumulate_timing(metrics, "resolve_targets", perf_counter() - started)
        self.cache.set_targets(graph_cache_key, target_class, subjects)
        return subjects
