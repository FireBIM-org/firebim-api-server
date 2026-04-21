from __future__ import annotations

from copy import deepcopy

from app.checks.evaluator_base import AtomicEvaluator
from app.checks.evaluator_direct import DirectAtomicEvaluator
from app.checks.result_models import AtomicOutcome, DecisionNode, EvalContext, GraphRef

try:
    from app.checks.evaluator_pyshacl import PyShaclAtomicEvaluator
except Exception:  # pragma: no cover
    PyShaclAtomicEvaluator = None  # type: ignore[assignment]


class HybridAtomicEvaluator(AtomicEvaluator):
    """
    Direct evaluator first; pySHACL only when needed.
    """

    def __init__(
        self,
        *,
        direct_evaluator: DirectAtomicEvaluator | None = None,
        fallback_evaluator: AtomicEvaluator | None = None,
    ) -> None:
        self.direct_evaluator = direct_evaluator or DirectAtomicEvaluator()
        if fallback_evaluator is not None:
            self.fallback_evaluator = fallback_evaluator
        elif PyShaclAtomicEvaluator is not None:
            self.fallback_evaluator = PyShaclAtomicEvaluator()
        else:
            self.fallback_evaluator = None

    def evaluate_many(
        self,
        *,
        graph_ref: GraphRef,
        node: DecisionNode,
        targets: list[str],
        ctx: EvalContext,
    ) -> dict[str, AtomicOutcome]:
        metrics = ctx.extra.setdefault("metrics", {})
        counters = metrics.setdefault("counters", {})

        if self.direct_evaluator.supports(node):
            counters["direct_evaluator_attempts"] = int(counters.get("direct_evaluator_attempts", 0)) + len(targets)
            outcome_map = self.direct_evaluator.evaluate_many(
                graph_ref=graph_ref,
                node=node,
                targets=targets,
                ctx=ctx,
            )
            for outcome in outcome_map.values():
                outcome.evidence.setdefault("execution_path", "direct")
            return outcome_map

        counters["direct_evaluator_fallbacks"] = int(counters.get("direct_evaluator_fallbacks", 0)) + len(targets)
        if self.fallback_evaluator is None:
            raise NotImplementedError("pySHACL fallback is not available in this deployment")

        outcome_map: dict[str, AtomicOutcome] = {}
        for target in list(dict.fromkeys(targets)):
            outcome = self.fallback_evaluator.evaluate(graph_ref, node, target, ctx)
            outcome.evidence.setdefault("execution_path", "pyshacl")
            outcome_map[target] = outcome
        return outcome_map

    def evaluate(
        self,
        graph_ref: GraphRef,
        node: DecisionNode,
        target: str,
        ctx: EvalContext,
    ) -> AtomicOutcome:
        outcome = self.evaluate_many(
            graph_ref=graph_ref,
            node=node,
            targets=[target],
            ctx=ctx,
        )[target]
        return deepcopy(outcome)
