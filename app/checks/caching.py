from __future__ import annotations

from dataclasses import dataclass, field

from rdflib import Graph


@dataclass
class TargetingCache:
    """
    Lightweight per-run in-memory cache for check execution.

    Scope:
    - parsed RDF graphs keyed by graph URI
    - resolved target nodes keyed by "<graph_uri>::<target_class>"

    Notes:
    - intentionally simple for v1
    - not process-safe or persistent
    - suitable as an execution cache inside one API request / one engine run
    """

    parsed_graphs: dict[str, Graph] = field(default_factory=dict)
    targets_by_class: dict[str, list[str]] = field(default_factory=dict)

    def make_target_class_key(self, graph_uri: str, target_class: str) -> str:
        return f"{graph_uri}::{target_class}"

    def get_parsed_graph(self, graph_uri: str) -> Graph | None:
        return self.parsed_graphs.get(graph_uri)

    def set_parsed_graph(self, graph_uri: str, graph: Graph) -> None:
        self.parsed_graphs[graph_uri] = graph

    def get_targets(self, graph_uri: str, target_class: str) -> list[str] | None:
        key = self.make_target_class_key(graph_uri, target_class)
        return self.targets_by_class.get(key)

    def set_targets(self, graph_uri: str, target_class: str, targets: list[str]) -> None:
        key = self.make_target_class_key(graph_uri, target_class)
        self.targets_by_class[key] = targets

    def clear(self) -> None:
        self.parsed_graphs.clear()
        self.targets_by_class.clear()
