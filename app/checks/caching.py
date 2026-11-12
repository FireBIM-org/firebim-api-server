from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from time import perf_counter
from typing import Any

from rdflib import Graph

from app.checks.rdfs_light import materialize
from app.core.config import settings

# Derived entries (targets, property values, transitions) are many per graph; allow this many per cached graph.
DERIVED_ENTRIES_PER_GRAPH = 2048


def accumulate_timing(metrics: dict[str, Any] | None, key: str, delta_seconds: float) -> None:
    if metrics is None:
        return
    timings = metrics.setdefault("timings_ms", {})
    timings[key] = round(float(timings.get(key, 0.0)) + (delta_seconds * 1000.0), 3)


def increment_counter(metrics: dict[str, Any] | None, key: str, step: int = 1) -> None:
    if metrics is None:
        return
    counters = metrics.setdefault("counters", {})
    counters[key] = int(counters.get(key, 0)) + step


def make_content_hash(text: str) -> str:
    return sha256((text or "").encode("utf-8")).hexdigest()


def make_graph_cache_key(graph_uri: str, ttl: str | None) -> str:
    return f"{graph_uri}::{make_content_hash(ttl or '')}"


def make_rule_cache_key(rule_ttl: str) -> str:
    return make_content_hash(rule_ttl or "")


class LRUCache(OrderedDict):
    """A thread-safe dict that keeps at most ``maxsize`` entries, dropping the least recently used first.

    ``get`` and item access count as use. ``on_evict(key)`` is called for every entry dropped for space.
    """

    def __init__(self, maxsize: int, on_evict: Callable[[Any], None] | None = None) -> None:
        super().__init__()
        self.maxsize = max(1, int(maxsize))
        self._on_evict = on_evict
        self._lock = threading.RLock()

    def __getitem__(self, key: Any) -> Any:
        with self._lock:
            value = super().__getitem__(key)
            self.move_to_end(key)
            return value

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key not in self:
                return default
            return self[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        evicted: list[Any] = []
        with self._lock:
            super().__setitem__(key, value)
            self.move_to_end(key)
            while len(self) > self.maxsize:
                oldest, _ = self.popitem(last=False)
                evicted.append(oldest)
        if self._on_evict is not None:
            for key_evicted in evicted:
                self._on_evict(key_evicted)

    def discard_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in [key for key in self if isinstance(key, str) and key.startswith(prefix)]:
                super().__delitem__(key)


@dataclass
class CheckExecutionCache:
    """
    Lightweight in-memory cache for check execution.

    Scope:
    - parsed RDF graphs keyed by graph URI + TTL hash
    - resolved targets keyed by graph cache key + target class
    - compiled rules keyed by rule TTL hash
    - property values keyed by graph cache key + target + property path

    Notes:
    - process-local only
    - bounded: at most ``max_entries`` graphs (and as many schemas, indexes and compiled rules) are kept, least
      recently used first out; evicting a graph also drops everything derived from it. Derived per-node entries
      are additionally capped at ``max_entries * DERIVED_ENTRIES_PER_GRAPH``.
    """

    max_entries: int = field(default_factory=lambda: settings.CHECK_CACHE_MAX_ENTRIES)
    parsed_graphs: LRUCache = field(init=False)
    targets_by_class: LRUCache = field(init=False)
    compiled_rules: LRUCache = field(init=False)
    property_values_by_target: LRUCache = field(init=False)
    graph_schemas: LRUCache = field(init=False)
    graph_indexes: LRUCache = field(init=False)
    transition_results: LRUCache = field(init=False)
    property_values_by_batch: LRUCache = field(init=False)

    def __post_init__(self) -> None:
        derived_max = self.max_entries * DERIVED_ENTRIES_PER_GRAPH
        self.parsed_graphs = LRUCache(self.max_entries, on_evict=self._forget_graph)
        self.graph_schemas = LRUCache(self.max_entries)
        self.graph_indexes = LRUCache(self.max_entries)
        self.compiled_rules = LRUCache(self.max_entries)
        self.targets_by_class = LRUCache(derived_max)
        self.property_values_by_target = LRUCache(derived_max)
        self.property_values_by_batch = LRUCache(derived_max)
        self.transition_results = LRUCache(derived_max)

    def _forget_graph(self, graph_cache_key: str) -> None:
        self.graph_schemas.pop(graph_cache_key, None)
        self.graph_indexes.pop(graph_cache_key, None)
        # Derived keys start with the graph cache key ("<key>::..." here, "<key>|..." for transitions).
        for derived in (
            self.targets_by_class,
            self.property_values_by_target,
            self.property_values_by_batch,
            self.transition_results,
        ):
            derived.discard_prefix(graph_cache_key)

    def get_or_parse_graph(
        self,
        *,
        graph_uri: str,
        ttl: str,
        metrics: dict[str, Any] | None = None,
    ) -> tuple[str, Graph]:
        cache_key = make_graph_cache_key(graph_uri, ttl)
        cached = self.parsed_graphs.get(cache_key)
        if cached is not None:
            increment_counter(metrics, "parsed_graph_cache_hits")
            return cache_key, cached

        started = perf_counter()
        graph = Graph()
        graph.parse(data=ttl, format="turtle")
        inferred = materialize(graph)
        if inferred:
            increment_counter(metrics, "rdfs_inferred_triples", inferred)
        accumulate_timing(metrics, "parse_graph", perf_counter() - started)
        increment_counter(metrics, "parsed_graph_cache_misses")
        self.parsed_graphs[cache_key] = graph
        return cache_key, graph

    def make_target_class_key(self, graph_cache_key: str, target_class: str) -> str:
        return f"{graph_cache_key}::{target_class}"

    def get_targets(self, graph_cache_key: str, target_class: str) -> list[str] | None:
        return self.targets_by_class.get(self.make_target_class_key(graph_cache_key, target_class))

    def set_targets(self, graph_cache_key: str, target_class: str, targets: list[str]) -> None:
        self.targets_by_class[self.make_target_class_key(graph_cache_key, target_class)] = list(targets)

    def make_property_value_key(
        self,
        graph_cache_key: str,
        target_node: str,
        property_path: str,
    ) -> str:
        return f"{graph_cache_key}::{target_node}::{property_path}"

    def get_property_values(
        self,
        graph_cache_key: str,
        target_node: str,
        property_path: str,
    ) -> list[Any] | None:
        values = self.property_values_by_target.get(
            self.make_property_value_key(graph_cache_key, target_node, property_path)
        )
        return list(values) if values is not None else None

    def set_property_values(
        self,
        graph_cache_key: str,
        target_node: str,
        property_path: str,
        values: list[Any],
    ) -> None:
        self.property_values_by_target[self.make_property_value_key(graph_cache_key, target_node, property_path)] = (
            list(values)
        )

    def make_batch_property_value_key(
        self,
        graph_cache_key: str,
        property_path: str,
    ) -> str:
        return f"{graph_cache_key}::batch::{property_path}"

    def get_batch_property_values(
        self,
        graph_cache_key: str,
        property_path: str,
    ) -> dict[str, list[Any]] | None:
        values = self.property_values_by_batch.get(self.make_batch_property_value_key(graph_cache_key, property_path))
        return deepcopy(values) if values is not None else None

    def set_batch_property_values(
        self,
        graph_cache_key: str,
        property_path: str,
        values: dict[str, list[Any]],
    ) -> None:
        self.property_values_by_batch[self.make_batch_property_value_key(graph_cache_key, property_path)] = deepcopy(
            values
        )

    def get_graph_schema(self, graph_cache_key: str) -> Any | None:
        return self.graph_schemas.get(graph_cache_key)

    def set_graph_schema(self, graph_cache_key: str, schema: Any) -> None:
        self.graph_schemas[graph_cache_key] = schema

    def get_graph_index(self, graph_cache_key: str) -> Any | None:
        return self.graph_indexes.get(graph_cache_key)

    def set_graph_index(self, graph_cache_key: str, index: Any) -> None:
        self.graph_indexes[graph_cache_key] = index

    def get_transition_result(self, transition_key: str) -> Any | None:
        return self.transition_results.get(transition_key)

    def set_transition_result(self, transition_key: str, result: Any) -> None:
        self.transition_results[transition_key] = result

    def get_compiled_rule(self, rule_ttl: str) -> Any | None:
        cached = self.compiled_rules.get(make_rule_cache_key(rule_ttl))
        return deepcopy(cached) if cached is not None else None

    def set_compiled_rule(self, rule_ttl: str, compiled_rule: Any) -> None:
        self.compiled_rules[make_rule_cache_key(rule_ttl)] = deepcopy(compiled_rule)

    def clear(self) -> None:
        self.parsed_graphs.clear()
        self.targets_by_class.clear()
        self.compiled_rules.clear()
        self.property_values_by_target.clear()
        self.graph_schemas.clear()
        self.graph_indexes.clear()
        self.transition_results.clear()
        self.property_values_by_batch.clear()


_DEFAULT_CACHE = CheckExecutionCache()


def get_default_execution_cache() -> CheckExecutionCache:
    return _DEFAULT_CACHE
