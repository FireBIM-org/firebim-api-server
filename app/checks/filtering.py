from __future__ import annotations

from collections.abc import Iterable

from app.checks.graph_index import GraphIndex


def filter_targets_by_classes(graph_index: GraphIndex, classes: Iterable[str]) -> set[str]:
    selected: set[str] = set()
    for class_uri in classes:
        selected.update(graph_index.targets_by_class.get(class_uri, []))
    return selected


def filter_targets_by_properties(graph_index: GraphIndex, properties: Iterable[str]) -> set[str]:
    selected: set[str] = set()
    for property_uri in properties:
        selected.update(graph_index.targets_having_property.get(property_uri, []))
    return selected


def neighborhood(graph_index: GraphIndex, seed_targets: Iterable[str]) -> set[str]:
    selected: set[str] = set()
    for target in seed_targets:
        selected.add(target)
        selected.update(graph_index.neighbors_by_target.get(target, set()))
    return selected
