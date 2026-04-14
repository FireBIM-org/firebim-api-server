from __future__ import annotations

from collections import deque
from hashlib import sha256
from time import perf_counter
from typing import Any

from app.checks.caching import CheckExecutionCache, accumulate_timing, get_default_execution_cache, increment_counter
from app.checks.execution_state import ExecutionContext, ResolvedTargetBatch, TransitionKey
from app.checks.result_models import DecisionNode, TargetRef


class PathwayResolver:
    def __init__(self, cache: CheckExecutionCache | None = None) -> None:
        self.cache = cache or get_default_execution_cache()

    def resolve_forward(
        self,
        *,
        current_targets: list[TargetRef],
        from_node: DecisionNode,
        to_node: DecisionNode,
        execution_context: ExecutionContext,
        metrics: dict[str, Any] | None = None,
    ) -> ResolvedTargetBatch:
        return self._resolve(
            direction="forward",
            current_targets=current_targets,
            from_node=from_node,
            to_node=to_node,
            execution_context=execution_context,
            metrics=metrics,
        )

    def resolve_backward(
        self,
        *,
        current_targets: list[TargetRef],
        from_node: DecisionNode,
        to_node: DecisionNode,
        execution_context: ExecutionContext,
        metrics: dict[str, Any] | None = None,
    ) -> ResolvedTargetBatch:
        return self._resolve(
            direction="backward",
            current_targets=current_targets,
            from_node=from_node,
            to_node=to_node,
            execution_context=execution_context,
            metrics=metrics,
        )

    def _resolve(
        self,
        *,
        direction: str,
        current_targets: list[TargetRef],
        from_node: DecisionNode,
        to_node: DecisionNode,
        execution_context: ExecutionContext,
        metrics: dict[str, Any] | None = None,
    ) -> ResolvedTargetBatch:
        started = perf_counter()
        deduped_targets = self._dedupe_targets(current_targets)
        if from_node.target_class == to_node.target_class:
            batch = ResolvedTargetBatch(
                targets=[
                    TargetRef(target_node=item.target_node, target_class=to_node.target_class)
                    for item in deduped_targets
                ],
                pairs={item.target_node: [item.target_node] for item in deduped_targets},
                direction=direction,
                resolution_mode="same_class",
            )
            duration_seconds = perf_counter() - started
            self._record_transition(
                from_node, to_node, batch, execution_context, duration_ms=round(duration_seconds * 1000.0, 3)
            )
            accumulate_timing(metrics, "resolve_transition", duration_seconds)
            return batch

        cache_key = TransitionKey(
            graph_cache_key=execution_context.graph_cache_key,
            from_node_id=from_node.node_id,
            to_node_id=to_node.node_id,
            direction=direction,
            target_signature=self._target_signature(deduped_targets),
        ).cache_key()
        cached = self.cache.get_transition_result(cache_key)
        if cached is not None:
            increment_counter(metrics, "transition_cache_hits")
            self._record_transition(from_node, to_node, cached, execution_context, duration_ms=0.0)
            return cached

        increment_counter(metrics, "transition_cache_misses")
        batch = self._resolve_uncached(
            direction=direction,
            current_targets=deduped_targets,
            from_node=from_node,
            to_node=to_node,
            execution_context=execution_context,
        )
        self.cache.set_transition_result(cache_key, batch)
        duration_seconds = perf_counter() - started
        self._record_transition(
            from_node, to_node, batch, execution_context, duration_ms=round(duration_seconds * 1000.0, 3)
        )
        accumulate_timing(metrics, "resolve_transition", duration_seconds)
        return batch

    def _resolve_uncached(
        self,
        *,
        direction: str,
        current_targets: list[TargetRef],
        from_node: DecisionNode,
        to_node: DecisionNode,
        execution_context: ExecutionContext,
    ) -> ResolvedTargetBatch:
        index = execution_context.graph_index
        schema = execution_context.schema
        guard = execution_context.explosion_guard
        if direction == "backward":
            resolution_attempts = [
                (
                    "backward_reverse_lookup",
                    index.reverse_adjacency,
                    schema.candidate_predicates(from_node.target_class, to_node.target_class, reverse=True),
                )
            ]
        else:
            resolution_attempts = [
                (
                    "forward_edge",
                    index.forward_adjacency,
                    schema.candidate_predicates(from_node.target_class, to_node.target_class),
                ),
                (
                    "reverse_edge",
                    index.reverse_adjacency,
                    schema.candidate_predicates(from_node.target_class, to_node.target_class, reverse=True),
                ),
            ]

        last_fallback_reason: str | None = None
        for resolution_mode, adjacency, predicate_candidates in resolution_attempts:
            batch = self._resolve_with_adjacency(
                current_targets=current_targets,
                to_node=to_node,
                guard=guard,
                adjacency=adjacency,
                predicate_candidates=predicate_candidates,
                classes_by_target=index.classes_by_target,
                direction=direction,
                resolution_mode=resolution_mode,
                execution_context=execution_context,
            )
            if batch.targets:
                return batch
            if batch.fallback_reason:
                last_fallback_reason = batch.fallback_reason

        return ResolvedTargetBatch(
            targets=[],
            pairs={target.target_node: [] for target in current_targets},
            direction=direction,
            predicates=[],
            resolution_mode=resolution_attempts[-1][0],
            fallback_reason=last_fallback_reason or "no_pathway_targets",
            truncated=False,
        )

    def _resolve_with_adjacency(
        self,
        *,
        current_targets: list[TargetRef],
        to_node: DecisionNode,
        guard,
        adjacency: dict[str, dict[str, set[str]]],
        predicate_candidates: list[str],
        classes_by_target: dict[str, list[str]],
        direction: str,
        resolution_mode: str,
        execution_context: ExecutionContext,
    ) -> ResolvedTargetBatch:
        resolved_pairs: dict[str, list[str]] = {}
        resolved_targets: dict[str, TargetRef] = {}
        truncated = False
        fallback_reason: str | None = None

        for current in current_targets:
            if len(current_targets) > guard.max_active_targets:
                execution_context.metrics.warnings.append(
                    f"Active target count {len(current_targets)} exceeded guard {guard.max_active_targets}."
                )
            neighbor_ids = self._resolve_neighbors(
                start_target=current.target_node,
                required_class=to_node.target_class,
                adjacency=adjacency,
                classes_by_target=classes_by_target,
                predicate_candidates=predicate_candidates,
                max_hops=guard.max_pathway_hops,
            )
            if not neighbor_ids and not predicate_candidates:
                fallback_reason = "no_schema_predicates"
            if len(neighbor_ids) > guard.max_transition_fanout:
                truncated = True
                neighbor_ids = neighbor_ids[: guard.max_transition_fanout]
            resolved_pairs[current.target_node] = neighbor_ids
            for neighbor_id in neighbor_ids:
                resolved_targets.setdefault(
                    neighbor_id,
                    TargetRef(target_node=neighbor_id, target_class=to_node.target_class),
                )
                if len(resolved_targets) >= guard.max_transition_results:
                    truncated = True
                    break
            if truncated and len(resolved_targets) >= guard.max_transition_results:
                break

        if not resolved_targets and fallback_reason is None:
            fallback_reason = "no_pathway_targets"

        return ResolvedTargetBatch(
            targets=sorted(resolved_targets.values(), key=lambda item: item.target_node),
            pairs=resolved_pairs,
            direction=direction,
            predicates=predicate_candidates,
            resolution_mode=resolution_mode,
            fallback_reason=fallback_reason,
            truncated=truncated,
        )

    def _resolve_neighbors(
        self,
        *,
        start_target: str,
        required_class: str | None,
        adjacency: dict[str, dict[str, set[str]]],
        classes_by_target: dict[str, list[str]],
        predicate_candidates: list[str],
        max_hops: int,
    ) -> list[str]:
        if not required_class:
            return []

        direct_hits: set[str] = set()
        outgoing = adjacency.get(start_target, {})
        predicates_to_try = predicate_candidates or sorted(outgoing)
        for predicate in predicates_to_try:
            for neighbor in outgoing.get(predicate, set()):
                if required_class in classes_by_target.get(neighbor, []):
                    direct_hits.add(neighbor)
        if direct_hits:
            return sorted(direct_hits)

        if max_hops <= 1:
            return []

        queue: deque[tuple[str, int]] = deque([(start_target, 0)])
        seen = {start_target}
        results: set[str] = set()
        while queue:
            target, depth = queue.popleft()
            if depth >= max_hops:
                continue
            outgoing = adjacency.get(target, {})
            predicates_to_try = predicate_candidates or sorted(outgoing)
            for predicate in predicates_to_try:
                for neighbor in outgoing.get(predicate, set()):
                    if neighbor in seen:
                        continue
                    seen.add(neighbor)
                    if required_class in classes_by_target.get(neighbor, []):
                        results.add(neighbor)
                    queue.append((neighbor, depth + 1))
        return sorted(results)

    def _dedupe_targets(self, targets: list[TargetRef]) -> list[TargetRef]:
        seen: dict[str, TargetRef] = {}
        for target in targets:
            seen.setdefault(target.target_node, target)
        return list(seen.values())

    def _target_signature(self, targets: list[TargetRef]) -> str:
        text = "|".join(sorted(item.target_node for item in targets))
        return sha256(text.encode("utf-8")).hexdigest()

    def _record_transition(
        self,
        from_node: DecisionNode,
        to_node: DecisionNode,
        batch: ResolvedTargetBatch,
        execution_context: ExecutionContext,
        duration_ms: float,
    ) -> None:
        transition_id = f"{from_node.node_id}->{to_node.node_id}:{batch.direction}"
        sample_pairs = [
            {
                "from_target": source,
                "to_targets": targets[:5],
                "to_target_count": len(targets),
            }
            for source, targets in list(batch.pairs.items())[:5]
        ]
        execution_context.metrics.record_transition(
            transition_id,
            len(batch.targets),
            batch.fanouts,
            {
                "direction": batch.direction,
                "from_node_id": from_node.node_id,
                "to_node_id": to_node.node_id,
                "from_class": from_node.target_class,
                "to_class": to_node.target_class,
                "from_property": from_node.property_path,
                "to_property": to_node.property_path,
                "candidate_predicates": list(batch.predicates),
                "resolution_mode": batch.resolution_mode,
                "fallback_reason": batch.fallback_reason,
                "truncated": batch.truncated,
                "input_target_count": len(batch.pairs),
                "sample_pairs": sample_pairs,
                "duration_ms": duration_ms,
            },
        )
