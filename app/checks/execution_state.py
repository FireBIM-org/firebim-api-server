from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from app.checks.result_models import CompiledRuleSet, GraphRef, RunOptions, TargetRef


@dataclass(frozen=True)
class TransitionKey:
    graph_cache_key: str
    from_node_id: str
    to_node_id: str
    direction: str
    target_signature: str

    def cache_key(self) -> str:
        return "|".join(
            [
                self.graph_cache_key,
                self.from_node_id,
                self.to_node_id,
                self.direction,
                self.target_signature,
            ]
        )


@dataclass
class ExplosionGuardConfig:
    max_active_targets: int = 20000
    max_transition_fanout: int = 2000
    max_transition_results: int = 50000
    max_pathway_hops: int = 2


@dataclass
class BindingFrame:
    bindings: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    def with_binding(self, node_id: str, target_node: str) -> BindingFrame:
        updated = dict(self.bindings)
        updated[node_id] = target_node
        return BindingFrame(bindings=updated, aliases=dict(self.aliases))


@dataclass
class DiagnosticsCollector:
    enabled: bool = False
    active_targets_per_node: dict[str, int] = field(default_factory=dict)
    resolved_targets_per_transition: dict[str, int] = field(default_factory=dict)
    transition_fanouts: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    transition_inspections: list[dict[str, Any]] = field(default_factory=list)
    node_inspections: list[dict[str, Any]] = field(default_factory=list)
    terminal_failures: list[dict[str, Any]] = field(default_factory=list)
    node_visit_counts: dict[str, int] = field(default_factory=dict)
    node_timings_ms: dict[str, float] = field(default_factory=dict)
    class_timings_ms: dict[str, float] = field(default_factory=dict)
    transition_timings_ms: dict[str, float] = field(default_factory=dict)
    completed_chains: list[dict[str, Any]] = field(default_factory=list)
    sequence_events: list[dict[str, Any]] = field(default_factory=list)
    _event_index: int = 0

    def _next_event_index(self) -> int:
        index = self._event_index
        self._event_index += 1
        return index

    def _record_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.sequence_events.append(
            {
                "event_index": self._next_event_index(),
                "event_type": event_type,
                **payload,
            }
        )

    def record_active_targets(self, node_id: str, count: int) -> None:
        if not self.enabled:
            return
        self.active_targets_per_node[node_id] = count
        self.node_inspections.append(
            {
                "node_id": node_id,
                "active_targets": count,
            }
        )
        self._record_event(
            "active_targets",
            {
                "node_id": node_id,
                "active_targets": count,
            },
        )

    def record_node_visit(
        self,
        node_id: str,
        *,
        node_type: str,
        target_class: str | None = None,
        property_path: str | None = None,
        sample_target: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.node_visit_counts[node_id] = int(self.node_visit_counts.get(node_id, 0)) + 1
        existing = next(
            (
                item
                for item in self.node_inspections
                if item.get("node_id") == node_id and item.get("node_type") == node_type
            ),
            None,
        )
        if existing is None:
            self.node_inspections.append(
                {
                    "node_id": node_id,
                    "node_type": node_type,
                    "target_class": target_class,
                    "property_path": property_path,
                    "visit_count": self.node_visit_counts[node_id],
                    "sample_target": sample_target,
                }
            )
        else:
            existing["visit_count"] = self.node_visit_counts[node_id]
            if sample_target and not existing.get("sample_target"):
                existing["sample_target"] = sample_target
        self._record_event(
            "node_visit",
            {
                "node_id": node_id,
                "node_type": node_type,
                "target_class": target_class,
                "property_path": property_path,
                "sample_target": sample_target,
                "visit_count": self.node_visit_counts[node_id],
            },
        )

    def record_node_timing(
        self,
        node_id: str,
        *,
        duration_ms: float,
        target_class: str | None = None,
        target_count: int | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.node_timings_ms[node_id] = round(float(self.node_timings_ms.get(node_id, 0.0)) + duration_ms, 3)
        if target_class:
            self.class_timings_ms[target_class] = round(
                float(self.class_timings_ms.get(target_class, 0.0)) + duration_ms, 3
            )
        self._record_event(
            "node_timing",
            {
                "node_id": node_id,
                "target_class": target_class,
                "target_count": target_count,
                "duration_ms": round(float(duration_ms), 3),
            },
        )

    def record_transition(
        self,
        transition_id: str,
        resolved_count: int,
        fanouts: list[int],
        details: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.resolved_targets_per_transition[transition_id] = resolved_count
        self.transition_fanouts.extend(fanouts)
        inspection = {
            "transition_id": transition_id,
            "resolved_targets": resolved_count,
            "fanout_avg": round(float(mean(fanouts)), 3) if fanouts else 0.0,
            "fanout_max": max(fanouts) if fanouts else 0,
        }
        if details:
            inspection.update(details)
        self.transition_inspections.append(inspection)
        duration_ms = float(inspection.get("duration_ms") or 0.0)
        self.transition_timings_ms[transition_id] = round(
            float(self.transition_timings_ms.get(transition_id, 0.0)) + duration_ms, 3
        )
        self._record_event("transition", inspection)

    def record_terminal_failure(self, details: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.terminal_failures.append(details)

    def record_chain_completion(self, chain: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.completed_chains.append(chain)
        self._record_event("chain_complete", chain)

    def to_debug_dict(self) -> dict[str, Any]:
        if not self.enabled:
            return {}
        fanout_avg = mean(self.transition_fanouts) if self.transition_fanouts else 0.0
        fanout_max = max(self.transition_fanouts) if self.transition_fanouts else 0
        return {
            "active_targets_per_node": dict(self.active_targets_per_node),
            "resolved_targets_per_transition": dict(self.resolved_targets_per_transition),
            "transition_fanout_avg": round(float(fanout_avg), 3),
            "transition_fanout_max": fanout_max,
            "warnings": list(self.warnings),
            "transition_inspections": list(self.transition_inspections),
            "node_inspections": list(self.node_inspections),
            "terminal_failures": list(self.terminal_failures),
            "node_visit_counts": dict(self.node_visit_counts),
            "node_timings_ms": dict(self.node_timings_ms),
            "class_timings_ms": dict(self.class_timings_ms),
            "transition_timings_ms": dict(self.transition_timings_ms),
            "completed_chains": list(self.completed_chains),
            "sequence_events": list(self.sequence_events),
        }


@dataclass
class ExecutionContext:
    graph_ref: GraphRef
    compiled: CompiledRuleSet
    options: RunOptions
    graph_cache_key: str
    graph_index: Any
    schema: Any
    plan: Any
    explosion_guard: ExplosionGuardConfig = field(default_factory=ExplosionGuardConfig)
    metrics: DiagnosticsCollector = field(default_factory=DiagnosticsCollector)


@dataclass
class ResolvedTargetBatch:
    targets: list[TargetRef]
    pairs: dict[str, list[str]] = field(default_factory=dict)
    direction: str = "forward"
    predicates: list[str] = field(default_factory=list)
    resolution_mode: str = "same_class"
    fallback_reason: str | None = None
    truncated: bool = False

    @property
    def fanouts(self) -> list[int]:
        return [len(items) for items in self.pairs.values()]
