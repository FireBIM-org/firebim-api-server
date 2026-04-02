from __future__ import annotations

from rdflib import Graph, RDF, URIRef

from app.checks.caching import TargetingCache
from app.checks.result_models import CompiledRuleSet, DecisionNode, GraphRef, TargetRef


class TargetResolver:
    """
    Resolve candidate targets from a building graph for compiled rules.

    Current strategy:
    - for each decision node, inspect target_class
    - collect RDF subjects of rdf:type target_class
    - cache per graph + target class
    - expose both:
        * per-node targets
        * union of all targets for a rule

    Notes:
    - intentionally simple for v1
    - independent from pySHACL runtime/evaluation
    - can later be extended with:
        * SHACL targetNode
        * SPARQL-based targets
        * subclass / ontology-aware expansion
        * persistent precomputed indexes
    """

    def __init__(self, cache: TargetingCache | None = None) -> None:
        self.cache = cache or TargetingCache()

    def resolve_rule_targets(
        self,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
    ) -> list[TargetRef]:
        """
        Return the union of all candidate targets referenced by the rule's
        decision nodes.
        """
        graph = self._get_graph(graph_ref)
        targets_by_node = self.resolve_targets_by_node(graph_ref, compiled, graph=graph)

        merged: dict[str, TargetRef] = {}
        for node_targets in targets_by_node.values():
            for target in node_targets:
                if target.target_node not in merged:
                    merged[target.target_node] = target

        return sorted(
            merged.values(),
            key=lambda item: (item.target_class or "", item.target_node),
        )

    def resolve_targets_by_node(
        self,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
        graph: Graph | None = None,
    ) -> dict[str, list[TargetRef]]:
        """
        Return candidate targets per decision node_id.
        """
        rdf_graph = graph or self._get_graph(graph_ref)

        result: dict[str, list[TargetRef]] = {}
        for node in compiled.nodes.values():
            if node.node_type != "decision":
                continue

            decision = node
            result[decision.node_id] = self.resolve_targets_for_decision(
                graph_ref=graph_ref,
                node=decision,
                graph=rdf_graph,
            )

        return result

    def resolve_targets_for_decision(
        self,
        graph_ref: GraphRef,
        node: DecisionNode,
        graph: Graph | None = None,
    ) -> list[TargetRef]:
        """
        Resolve candidate targets for a single decision node.
        """
        if not node.target_class:
            return []

        rdf_graph = graph or self._get_graph(graph_ref)
        target_nodes = self._resolve_subjects_by_target_class(
            graph_ref=graph_ref,
            graph=rdf_graph,
            target_class=node.target_class,
        )

        return [
            TargetRef(
                target_node=target_node,
                target_class=node.target_class,
            )
            for target_node in target_nodes
        ]

    def estimate_target_counts(
        self,
        graph_ref: GraphRef,
        compiled: CompiledRuleSet,
    ) -> dict[str, int]:
        """
        Return target counts per decision node_id.
        """
        graph = self._get_graph(graph_ref)
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
            )
            counts[node.node_id] = len(targets)

        return counts

    def collect_target_classes(self, compiled: CompiledRuleSet) -> list[str]:
        """
        Return the distinct target classes referenced by decision nodes.
        """
        target_classes = {
            node.target_class
            for node in compiled.nodes.values()
            if node.node_type == "decision" and node.target_class
        }
        return sorted(target_classes)

    def _get_graph(self, graph_ref: GraphRef) -> Graph:
        cached = self.cache.get_parsed_graph(graph_ref.graph_uri)
        if cached is not None:
            return cached

        if not graph_ref.ttl or not graph_ref.ttl.strip():
            raise ValueError(
                f"Graph '{graph_ref.graph_id}' has no TTL content loaded. "
                "TargetResolver currently requires graph_ref.ttl."
            )

        graph = Graph()
        graph.parse(data=graph_ref.ttl, format="turtle")
        self.cache.set_parsed_graph(graph_ref.graph_uri, graph)
        return graph

    def _resolve_subjects_by_target_class(
        self,
        graph_ref: GraphRef,
        graph: Graph,
        target_class: str,
    ) -> list[str]:
        cached = self.cache.get_targets(graph_ref.graph_uri, target_class)
        if cached is not None:
            return cached

        target_uri = URIRef(target_class)
        subjects = sorted(
            {str(subject) for subject in graph.subjects(RDF.type, target_uri)}
        )

        self.cache.set_targets(graph_ref.graph_uri, target_class, subjects)
        return subjects
