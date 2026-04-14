from __future__ import annotations

from typing import Any, TypedDict

from app.checks.graph_index import GraphIndexBuilder
from app.checks.result_models import GraphRef


class SchemaDebugObjects(TypedDict):
    schema: Any
    graph_index: Any


class GraphSchemaPayload(TypedDict):
    graph_id: str
    graph_uri: str
    triple_count: int
    class_counts: dict[str, int]
    property_counts: dict[str, int]
    class_to_class_edges: list[dict[str, Any]]
    forward_predicates_by_class_pair: dict[str, list[str]]
    reverse_predicates_by_class_pair: dict[str, list[str]]
    warnings: list[str]


class GraphSchemaExtraction(TypedDict):
    payload: GraphSchemaPayload
    debug_objects: SchemaDebugObjects


def extract_graph_schema_payload(
    *,
    graph_id: str,
    graph_uri: str,
    graph_ttl: str,
    include_examples: bool = False,
    max_examples_per_edge: int = 3,
    metrics: dict[str, Any] | None = None,
) -> GraphSchemaExtraction:
    builder = GraphIndexBuilder()
    graph_ref = GraphRef(graph_id=graph_id, graph_uri=graph_uri, ttl=graph_ttl)
    graph_index = builder.build(graph_ref=graph_ref, metrics=metrics)
    schema = builder.schema_extractor.extract(
        graph_cache_key=graph_index.graph_cache_key,
        graph=graph_index.graph,
        include_examples=include_examples,
        max_examples_per_edge=max_examples_per_edge,
        metrics=metrics,
    )
    return {
        "payload": {
            "graph_id": graph_id,
            "graph_uri": graph_uri,
            "triple_count": schema.triple_count,
            "class_counts": schema.instance_counts_by_class,
            "property_counts": schema.predicate_usage,
            "class_to_class_edges": [
                edge.to_dict()
                for edge in sorted(
                    schema.class_to_class_edges.values(),
                    key=lambda item: (item.from_class, item.to_class),
                )
            ],
            "forward_predicates_by_class_pair": schema.forward_predicates_by_class_pair,
            "reverse_predicates_by_class_pair": schema.reverse_predicates_by_class_pair,
            "warnings": schema.warnings,
        },
        "debug_objects": {
            "schema": schema,
            "graph_index": graph_index,
        },
    }
