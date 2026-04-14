from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from rdflib import RDF, Graph, URIRef

from app.checks.caching import CheckExecutionCache, accumulate_timing, get_default_execution_cache, increment_counter


@dataclass
class GraphSchemaEdge:
    from_class: str
    to_class: str
    predicates: set[str] = field(default_factory=set)
    support: int = 0
    examples: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_class": self.from_class,
            "to_class": self.to_class,
            "predicates": sorted(self.predicates),
            "support": self.support,
            "examples": list(self.examples),
        }


@dataclass
class GraphDerivedSchema:
    triple_count: int
    classes_present: list[str]
    properties_present: list[str]
    instance_counts_by_class: dict[str, int]
    typed_subjects_by_class: dict[str, list[str]]
    subject_class_pairs: dict[str, list[str]]
    predicate_usage: dict[str, int]
    class_to_class_edges: dict[tuple[str, str], GraphSchemaEdge]
    forward_predicates_by_class_pair: dict[str, list[str]]
    reverse_predicates_by_class_pair: dict[str, list[str]]
    warnings: list[str] = field(default_factory=list)

    def candidate_predicates(self, from_class: str | None, to_class: str | None, *, reverse: bool = False) -> list[str]:
        if not from_class or not to_class:
            return []
        key = f"{from_class}|{to_class}" if not reverse else f"{to_class}|{from_class}"
        mapping = self.reverse_predicates_by_class_pair if reverse else self.forward_predicates_by_class_pair
        return list(mapping.get(key, []))

    def edge_count(self) -> int:
        return len(self.class_to_class_edges)

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "triple_count": self.triple_count,
            "schema_class_count": len(self.classes_present),
            "schema_edge_count": self.edge_count(),
            "warnings": list(self.warnings),
        }


class GraphSchemaExtractor:
    def __init__(self, cache: CheckExecutionCache | None = None) -> None:
        self.cache = cache or get_default_execution_cache()

    def extract(
        self,
        *,
        graph_cache_key: str,
        graph: Graph,
        include_examples: bool = False,
        max_examples_per_edge: int = 3,
        metrics: dict[str, Any] | None = None,
    ) -> GraphDerivedSchema:
        cached = self.cache.get_graph_schema(graph_cache_key)
        if cached is not None and (
            not include_examples or any(edge.examples for edge in cached.class_to_class_edges.values())
        ):
            increment_counter(metrics, "graph_schema_cache_hits")
            return cached

        increment_counter(metrics, "graph_schema_cache_misses")
        started = perf_counter()
        typed_subjects: dict[str, set[str]] = defaultdict(set)
        classes_by_subject: dict[str, set[str]] = defaultdict(set)
        predicate_usage: Counter[str] = Counter()
        edge_map: dict[tuple[str, str], GraphSchemaEdge] = {}
        warnings: list[str] = []

        for subj, pred, obj in graph:
            subj_str = str(subj)
            pred_str = str(pred)
            predicate_usage[pred_str] += 1
            if pred == RDF.type:
                class_str = str(obj)
                typed_subjects[class_str].add(subj_str)
                classes_by_subject[subj_str].add(class_str)

        untyped_subject_count = 0
        for subj, pred, obj in graph:
            if pred == RDF.type:
                continue
            subj_str = str(subj)
            if not classes_by_subject.get(subj_str):
                untyped_subject_count += 1
            if not isinstance(obj, URIRef):
                continue
            obj_str = str(obj)
            subject_classes = classes_by_subject.get(subj_str, set())
            object_classes = classes_by_subject.get(obj_str, set())
            for from_class in subject_classes:
                for to_class in object_classes:
                    key = (from_class, to_class)
                    edge = edge_map.setdefault(
                        key,
                        GraphSchemaEdge(from_class=from_class, to_class=to_class),
                    )
                    edge.predicates.add(str(pred))
                    edge.support += 1
                    if include_examples and len(edge.examples) < max_examples_per_edge:
                        edge.examples.append(
                            {
                                "subject": subj_str,
                                "predicate": str(pred),
                                "object": obj_str,
                            }
                        )

        if untyped_subject_count:
            warnings.append(
                f"Observed {untyped_subject_count} non-type triples whose subject had no rdf:type; schema is partial."
            )

        forward_pairs = {
            f"{from_class}|{to_class}": sorted(edge.predicates) for (from_class, to_class), edge in edge_map.items()
        }
        reverse_pairs = {
            f"{from_class}|{to_class}": sorted(edge.predicates) for (to_class, from_class), edge in edge_map.items()
        }

        schema = GraphDerivedSchema(
            triple_count=len(graph),
            classes_present=sorted(typed_subjects),
            properties_present=sorted(predicate_usage),
            instance_counts_by_class={class_uri: len(subjects) for class_uri, subjects in typed_subjects.items()},
            typed_subjects_by_class={class_uri: sorted(subjects) for class_uri, subjects in typed_subjects.items()},
            subject_class_pairs={subject: sorted(classes) for subject, classes in classes_by_subject.items()},
            predicate_usage=dict(predicate_usage),
            class_to_class_edges=edge_map,
            forward_predicates_by_class_pair=forward_pairs,
            reverse_predicates_by_class_pair=reverse_pairs,
            warnings=warnings,
        )
        self.cache.set_graph_schema(graph_cache_key, schema)
        accumulate_timing(metrics, "extract_schema", perf_counter() - started)
        return schema
