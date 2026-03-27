from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from rdflib import Graph

from app.checks.result_models import (
    AtomicOutcome,
    DecisionNode,
    EvalContext,
    GraphRef,
)


class AtomicEvaluator(ABC):
    """
    Abstract interface for evaluating one atomic decision node
    against one concrete target in one graph context.

    Contract:
    - input:
        * graph_ref: reference to the building graph
        * node: compiled decision node
        * target: URI string of the focus node
        * ctx: execution/evaluation context
    - output:
        * AtomicOutcome with explicit True / False / Unknown

    Important:
    - Unknown is an explicit result, not absence of failure
    - evaluator does NOT perform traversal
    - evaluator does NOT aggregate rule summaries
    - evaluator should return evidence helpful for explainability
    """

    @abstractmethod
    def evaluate(
        self,
        graph_ref: GraphRef,
        node: DecisionNode,
        target: str,
        ctx: EvalContext,
    ) -> AtomicOutcome:
        raise NotImplementedError


class RDFGraphBackedEvaluator(AtomicEvaluator, ABC):
    """
    Base class for evaluators that need the parsed RDF graph.

    This provides:
    - TTL -> rdflib Graph loading
    - optional graph reuse inside one evaluator instance
    - shared error handling helpers
    """

    def __init__(self) -> None:
        self._graph_cache: dict[str, Graph] = {}

    def get_graph(self, graph_ref: GraphRef) -> Graph:
        cached = self._graph_cache.get(graph_ref.graph_uri)
        if cached is not None:
            return cached

        if not graph_ref.ttl or not graph_ref.ttl.strip():
            raise ValueError(
                f"Graph '{graph_ref.graph_id}' has no TTL content loaded"
            )

        graph = Graph()
        graph.parse(data=graph_ref.ttl, format="turtle")
        self._graph_cache[graph_ref.graph_uri] = graph
        return graph

    def clear_cache(self) -> None:
        self._graph_cache.clear()

    def unknown_outcome(
        self,
        *,
        reason_code: str,
        message: str | None = None,
        evidence: dict[str, Any] | None = None,
        raw_report_ttl: str | None = None,
    ) -> AtomicOutcome:
        return AtomicOutcome(
            status="Unknown",
            reason_code=reason_code,
            message=message,
            evidence=evidence or {},
            raw_report_ttl=raw_report_ttl,
        )

    def true_outcome(
        self,
        *,
        reason_code: str | None = None,
        message: str | None = None,
        evidence: dict[str, Any] | None = None,
        raw_report_ttl: str | None = None,
    ) -> AtomicOutcome:
        return AtomicOutcome(
            status="True",
            reason_code=reason_code,
            message=message,
            evidence=evidence or {},
            raw_report_ttl=raw_report_ttl,
        )

    def false_outcome(
        self,
        *,
        reason_code: str | None = None,
        message: str | None = None,
        evidence: dict[str, Any] | None = None,
        raw_report_ttl: str | None = None,
    ) -> AtomicOutcome:
        return AtomicOutcome(
            status="False",
            reason_code=reason_code,
            message=message,
            evidence=evidence or {},
            raw_report_ttl=raw_report_ttl,
        )

    def evaluator_error_outcome(
        self,
        *,
        node: DecisionNode,
        target: str,
        exc: Exception,
        extra_evidence: dict[str, Any] | None = None,
    ) -> AtomicOutcome:
        evidence: dict[str, Any] = {
            "node_id": node.node_id,
            "target": target,
            "target_class": node.target_class,
            "property_path": node.property_path,
            "operator": node.operator,
            "expected_value": node.value,
            "error_type": exc.__class__.__name__,
            "error_text": str(exc),
        }
        if extra_evidence:
            evidence.update(extra_evidence)

        return self.unknown_outcome(
            reason_code="evaluator_error",
            message=f"Evaluator failed for node '{node.node_id}' and target '{target}'",
            evidence=evidence,
        )


class NoOpEvaluator(AtomicEvaluator):
    """
    Minimal evaluator for scaffolding and tests.

    Behavior:
    - always returns Unknown
    - useful before plugging in pySHACL/Jena/SPARQL implementations
    """

    def evaluate(
        self,
        graph_ref: GraphRef,
        node: DecisionNode,
        target: str,
        ctx: EvalContext,
    ) -> AtomicOutcome:
        return AtomicOutcome(
            status="Unknown",
            reason_code="not_implemented",
            message="No evaluator backend configured",
            evidence={
                "graph_id": graph_ref.graph_id,
                "graph_uri": graph_ref.graph_uri,
                "node_id": node.node_id,
                "target": target,
                "target_class": node.target_class,
                "property_path": node.property_path,
                "operator": node.operator,
                "expected_value": node.value,
                "ctx_rule_id": ctx.rule_id,
                "ctx_step_index": ctx.current_step_index,
            },
        )