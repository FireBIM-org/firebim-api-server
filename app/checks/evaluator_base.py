from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from rdflib import RDF, Graph, Literal, URIRef

from app.checks.caching import CheckExecutionCache, get_default_execution_cache
from app.checks.derived import DerivedOverlay, resolve_path_values
from app.checks.result_models import (
    AtomicOutcome,
    DecisionNode,
    EvalContext,
    GraphRef,
)
from app.rules.paths import expand_property_path


class AtomicEvaluator(ABC):
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
    def __init__(self, cache: CheckExecutionCache | None = None) -> None:
        self.cache = cache or get_default_execution_cache()

    def _metrics(self, ctx: EvalContext | None) -> dict[str, Any] | None:
        if ctx is None:
            return None
        return ctx.extra.setdefault("metrics", {})

    def get_graph(self, graph_ref: GraphRef, ctx: EvalContext | None = None) -> Graph:
        if not graph_ref.ttl or not graph_ref.ttl.strip():
            raise ValueError(f"Graph '{graph_ref.graph_id}' has no TTL content loaded")

        _cache_key, graph = self.cache.get_or_parse_graph(
            graph_uri=graph_ref.graph_uri,
            ttl=graph_ref.ttl,
            metrics=self._metrics(ctx),
        )
        return graph

    def get_property_values(
        self,
        *,
        graph_ref: GraphRef,
        target: str,
        property_path: str,
        ctx: EvalContext,
    ) -> list[Any]:
        metrics = self._metrics(ctx)
        if not graph_ref.ttl or not graph_ref.ttl.strip():
            raise ValueError(f"Graph '{graph_ref.graph_id}' has no TTL content loaded")

        graph_cache_key, graph = self.cache.get_or_parse_graph(
            graph_uri=graph_ref.graph_uri,
            ttl=graph_ref.ttl,
            metrics=metrics,
        )
        cached = self.cache.get_property_values(graph_cache_key, target, property_path)
        if cached is not None:
            counters = metrics.setdefault("counters", {}) if metrics is not None else None
            if counters is not None:
                counters["property_value_cache_hits"] = int(counters.get("property_value_cache_hits", 0)) + 1
            return cached

        counters = metrics.setdefault("counters", {}) if metrics is not None else None
        if counters is not None:
            counters["property_value_cache_misses"] = int(counters.get("property_value_cache_misses", 0)) + 1

        segments = expand_property_path(property_path, self._prefixes(ctx))
        overlay = self._overlay(ctx)
        if overlay is not None and self._overlay_touches(overlay, segments):
            # never cache overlay-shadowed reads
            return list(resolve_path_values(graph, target, segments, overlay))
        values = list(resolve_path_values(graph, target, segments, None))
        self.cache.set_property_values(graph_cache_key, target, property_path, values)
        return list(values)

    @staticmethod
    def _overlay(ctx: EvalContext | None) -> DerivedOverlay | None:
        if ctx is None:
            return None
        overlay = ctx.extra.get("overlay")
        return overlay if isinstance(overlay, DerivedOverlay) else None

    @staticmethod
    def _prefixes(ctx: EvalContext | None) -> dict[str, str] | None:
        if ctx is None:
            return None
        prefixes = ctx.extra.get("prefixes")
        return prefixes if isinstance(prefixes, dict) else None

    @staticmethod
    def _overlay_touches(overlay: DerivedOverlay, segments: list[str]) -> bool:
        return any(overlay.shadows(seg) for seg in segments)

    def prefetch_property_values(
        self,
        *,
        graph_ref: GraphRef,
        targets: list[str],
        property_path: str,
        metrics: dict[str, Any] | None = None,
        overlay: DerivedOverlay | None = None,
        prefixes: dict[str, str] | None = None,
    ) -> dict[str, list[Any]]:
        if not graph_ref.ttl or not graph_ref.ttl.strip():
            raise ValueError(f"Graph '{graph_ref.graph_id}' has no TTL content loaded")

        graph_cache_key, graph = self.cache.get_or_parse_graph(
            graph_uri=graph_ref.graph_uri,
            ttl=graph_ref.ttl,
            metrics=metrics,
        )
        segments = expand_property_path(property_path, prefixes)
        if overlay is not None and self._overlay_touches(overlay, segments):
            # overlay-shadowed: resolve live, bypass every cache
            return {
                target: list(resolve_path_values(graph, target, segments, overlay))
                for target in list(dict.fromkeys(targets))
            }
        cached = self.cache.get_batch_property_values(graph_cache_key, property_path)
        if cached is None:
            batch_values: dict[str, list[Any]] = {}
            for target in list(dict.fromkeys(targets)):
                values = list(resolve_path_values(graph, target, segments, None))
                batch_values[target] = values
                self.cache.set_property_values(graph_cache_key, target, property_path, values)
            self.cache.set_batch_property_values(graph_cache_key, property_path, batch_values)
            return batch_values

        result: dict[str, list[Any]] = {}
        for target in list(dict.fromkeys(targets)):
            values = cached.get(target)
            if values is None:
                values = self.get_property_values(
                    graph_ref=graph_ref,
                    target=target,
                    property_path=property_path,
                    ctx=EvalContext(
                        rule_id="__prefetch__",
                        graph_id=graph_ref.graph_id,
                        graph_uri=graph_ref.graph_uri,
                        extra={"metrics": metrics or {}},
                    ),
                )
            result[target] = list(values)
        return result

    def target_has_type(
        self,
        *,
        graph_ref: GraphRef,
        target: str,
        target_class: str,
        ctx: EvalContext,
    ) -> bool:
        graph = self.get_graph(graph_ref, ctx)
        return any(graph.triples((URIRef(target), RDF.type, URIRef(target_class))))

    def clear_cache(self) -> None:
        self.cache.clear()

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

    def term_to_python(self, value: Any) -> Any:
        if isinstance(value, Literal):
            return value.toPython()
        return str(value)


class NoOpEvaluator(AtomicEvaluator):
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
