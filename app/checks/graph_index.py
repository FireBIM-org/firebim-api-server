from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from rdflib import RDF, Graph, URIRef

from app.checks.caching import CheckExecutionCache, accumulate_timing, get_default_execution_cache, increment_counter
from app.checks.graph_schema import GraphDerivedSchema, GraphSchemaExtractor
from app.checks.result_models import GraphRef, TargetRef


@dataclass
class GraphIndex:
    graph_cache_key: str
    graph: Graph
    targets_by_class: dict[str, list[str]]
    classes_by_target: dict[str, list[str]]
    property_values_by_target: dict[str, dict[str, list[Any]]]
    targets_having_property: dict[str, list[str]]
    forward_adjacency: dict[str, dict[str, set[str]]]
    reverse_adjacency: dict[str, dict[str, set[str]]]
    neighbors_by_target: dict[str, set[str]]
    schema: GraphDerivedSchema

    def target_refs_for_class(self, target_class: str | None) -> list[TargetRef]:
        if not target_class:
            return []
        return [
            TargetRef(target_node=target, target_class=target_class)
            for target in self.targets_by_class.get(target_class, [])
        ]


class GraphIndexBuilder:
    def __init__(
        self,
        cache: CheckExecutionCache | None = None,
        schema_extractor: GraphSchemaExtractor | None = None,
    ) -> None:
        self.cache = cache or get_default_execution_cache()
        self.schema_extractor = schema_extractor or GraphSchemaExtractor(cache=self.cache)

    def build(
        self,
        *,
        graph_ref: GraphRef,
        metrics: dict[str, Any] | None = None,
    ) -> GraphIndex:
        if not graph_ref.ttl or not graph_ref.ttl.strip():
            raise ValueError(f"Graph '{graph_ref.graph_id}' has no TTL content loaded")

        graph_cache_key, graph = self.cache.get_or_parse_graph(
            graph_uri=graph_ref.graph_uri,
            ttl=graph_ref.ttl,
            metrics=metrics,
        )
        cached = self.cache.get_graph_index(graph_cache_key)
        if cached is not None:
            increment_counter(metrics, "graph_index_cache_hits")
            return cached

        increment_counter(metrics, "graph_index_cache_misses")
        started = perf_counter()
        targets_by_class: dict[str, set[str]] = defaultdict(set)
        classes_by_target: dict[str, set[str]] = defaultdict(set)
        property_values_by_target: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
        targets_having_property: dict[str, set[str]] = defaultdict(set)
        forward_adjacency: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        reverse_adjacency: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        neighbors_by_target: dict[str, set[str]] = defaultdict(set)

        for subj, pred, obj in graph:
            subj_str = str(subj)
            pred_str = str(pred)
            if pred == RDF.type:
                class_str = str(obj)
                targets_by_class[class_str].add(subj_str)
                classes_by_target[subj_str].add(class_str)
                continue

            property_values_by_target[subj_str][pred_str].append(obj)
            targets_having_property[pred_str].add(subj_str)
            if isinstance(obj, URIRef):
                obj_str = str(obj)
                forward_adjacency[subj_str][pred_str].add(obj_str)
                reverse_adjacency[obj_str][pred_str].add(subj_str)
                neighbors_by_target[subj_str].add(obj_str)
                neighbors_by_target[obj_str].add(subj_str)

        schema = self.schema_extractor.extract(
            graph_cache_key=graph_cache_key,
            graph=graph,
            metrics=metrics,
        )

        index = GraphIndex(
            graph_cache_key=graph_cache_key,
            graph=graph,
            targets_by_class={class_uri: sorted(targets) for class_uri, targets in targets_by_class.items()},
            classes_by_target={target: sorted(classes) for target, classes in classes_by_target.items()},
            property_values_by_target={
                target: {prop: list(values) for prop, values in props.items()}
                for target, props in property_values_by_target.items()
            },
            targets_having_property={prop: sorted(targets) for prop, targets in targets_having_property.items()},
            forward_adjacency={
                target: {pred: set(values) for pred, values in pred_map.items()}
                for target, pred_map in forward_adjacency.items()
            },
            reverse_adjacency={
                target: {pred: set(values) for pred, values in pred_map.items()}
                for target, pred_map in reverse_adjacency.items()
            },
            neighbors_by_target={target: set(values) for target, values in neighbors_by_target.items()},
            schema=schema,
        )
        self.cache.set_graph_index(graph_cache_key, index)
        accumulate_timing(metrics, "build_index", perf_counter() - started)
        return index
