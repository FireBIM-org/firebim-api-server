from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from rdflib import RDFS, Graph


@dataclass(frozen=True)
class LexiconCustomEvaluator:
    graph: Graph

    def __post_init__(self) -> None:
        parents_by_uri: dict[str, set[str]] = defaultdict(set)
        children_by_uri: dict[str, set[str]] = defaultdict(set)
        for child, _, parent in self.graph.triples((None, RDFS.subClassOf, None)):
            parents_by_uri[str(child)].add(str(parent))
            children_by_uri[str(parent)].add(str(child))
        object.__setattr__(self, "_parents_by_uri", parents_by_uri)
        object.__setattr__(self, "_children_by_uri", children_by_uri)

    def is_same_or_subclass(self, candidate_uri: str, target_uri: str) -> bool:
        if candidate_uri == target_uri:
            return True
        visited: set[str] = set()
        stack = [candidate_uri]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for parent_uri in self._parents_by_uri.get(current, set()):
                if parent_uri == target_uri:
                    return True
                stack.append(parent_uri)
        return False

    def ancestor_closure(self, candidate_uri: str) -> set[str]:
        ancestors: set[str] = {candidate_uri}
        stack = [candidate_uri]
        while stack:
            current = stack.pop()
            for parent_uri in self._parents_by_uri.get(current, set()):
                if parent_uri in ancestors:
                    continue
                ancestors.add(parent_uri)
                stack.append(parent_uri)
        return ancestors

    def shared_hierarchy(self, left_uri: str, right_uri: str) -> bool:
        return self.is_same_or_subclass(left_uri, right_uri) or self.is_same_or_subclass(right_uri, left_uri)

    def subjects_for_target_class(
        self,
        subject_types: dict[str, set[str]],
        target_uri: str,
    ) -> list[str]:
        return sorted(
            subject
            for subject, types in subject_types.items()
            if any(self.is_same_or_subclass(candidate_uri, target_uri) for candidate_uri in types)
        )
