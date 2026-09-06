from __future__ import annotations

from numbers import Number
from typing import Any

from rdflib import RDF, URIRef

from app.checks.evaluator_base import RDFGraphBackedEvaluator
from app.checks.result_models import AtomicOutcome, DecisionNode, EvalContext, GraphRef
from app.rules.paths import is_ref_value, ref_path


class DirectAtomicEvaluator(RDFGraphBackedEvaluator):
    """
    Fast-path evaluator for the common FireBIM atomic decision shape.

    Supported directly:
    - one target class
    - one direct property path
    - one scalar comparison / equality / membership / between
    """

    SUPPORTED_OPERATORS = {"<", "<=", ">", ">=", "=", "==", "between"}

    def supports(self, node: DecisionNode) -> bool:
        if not (node.target_class and node.property_path and node.operator in self.SUPPORTED_OPERATORS):
            return False
        return not (is_ref_value(node.value) and node.operator == "between")

    def evaluate_many(
        self,
        *,
        graph_ref: GraphRef,
        node: DecisionNode,
        targets: list[str],
        ctx: EvalContext,
    ) -> dict[str, AtomicOutcome]:
        if not self.supports(node):
            raise NotImplementedError(
                f"Direct evaluator does not support operator '{node.operator}' or missing node metadata"
            )

        target_class_ref = URIRef(node.target_class)
        expected = node.value
        unique_targets = list(dict.fromkeys(targets))
        outcomes: dict[str, AtomicOutcome] = {}
        evaluation_shape = str(ctx.extra.get("evaluation_shape", "batch"))
        overlay = self._overlay(ctx)
        prefixes = self._prefixes(ctx)
        prefetched_values = self.prefetch_property_values(
            graph_ref=graph_ref,
            targets=unique_targets,
            property_path=node.property_path or "",
            metrics=ctx.extra.setdefault("metrics", {}),
            overlay=overlay,
            prefixes=prefixes,
        )
        reference_path = ref_path(expected) if is_ref_value(expected) else None
        prefetched_refs: dict[str, list[Any]] = {}
        if reference_path:
            prefetched_refs = self.prefetch_property_values(
                graph_ref=graph_ref,
                targets=unique_targets,
                property_path=reference_path,
                metrics=ctx.extra.setdefault("metrics", {}),
                overlay=overlay,
                prefixes=prefixes,
            )
        graph = self.get_graph(graph_ref, ctx)

        for target in unique_targets:
            try:
                target_ref = URIRef(target)
                if not any(graph.triples((target_ref, RDF.type, target_class_ref))):
                    outcomes[target] = self.unknown_outcome(
                        reason_code="target_not_instance_of_target_class",
                        message=(
                            f"Target '{target}' is not typed as required class '{node.target_class}' "
                            f"for node '{node.node_id}'"
                        ),
                        evidence={
                            "engine": "direct",
                            "node_id": node.node_id,
                            "target": target,
                            "target_class": node.target_class,
                            "evaluation_shape": evaluation_shape,
                        },
                    )
                    continue

                values = list(prefetched_values.get(target, []))
                if not values:
                    outcomes[target] = self.unknown_outcome(
                        reason_code="missing_required_input",
                        message=(
                            f"Target '{target}' has no value for required property "
                            f"'{node.property_path}' in node '{node.node_id}'"
                        ),
                        evidence={
                            "engine": "direct",
                            "node_id": node.node_id,
                            "target": target,
                            "target_class": node.target_class,
                            "property_path": node.property_path,
                            "evaluation_shape": evaluation_shape,
                        },
                    )
                    continue

                if len(values) > 1:
                    outcomes[target] = self.unknown_outcome(
                        reason_code="ambiguous_input",
                        message=(
                            f"Target '{target}' has multiple values for property "
                            f"'{node.property_path}' in node '{node.node_id}'"
                        ),
                        evidence={
                            "engine": "direct",
                            "node_id": node.node_id,
                            "target": target,
                            "property_path": node.property_path,
                            "values": [self.term_to_python(v) for v in values],
                            "evaluation_shape": evaluation_shape,
                        },
                    )
                    continue

                actual = self.term_to_python(values[0])
                expected_for_target = expected
                if reference_path:
                    ref_values = list(prefetched_refs.get(target, []))
                    if not ref_values:
                        outcomes[target] = self.unknown_outcome(
                            reason_code="missing_reference_input",
                            message=(
                                f"Target '{target}' has no value for reference path '{reference_path}' "
                                f"in node '{node.node_id}'"
                            ),
                            evidence={
                                "engine": "direct",
                                "node_id": node.node_id,
                                "target": target,
                                "property_path": node.property_path,
                                "reference_path": reference_path,
                                "evaluation_shape": evaluation_shape,
                            },
                        )
                        continue
                    if len(ref_values) > 1:
                        outcomes[target] = self.unknown_outcome(
                            reason_code="ambiguous_reference_input",
                            message=(
                                f"Target '{target}' has multiple values for reference path '{reference_path}' "
                                f"in node '{node.node_id}'"
                            ),
                            evidence={
                                "engine": "direct",
                                "node_id": node.node_id,
                                "target": target,
                                "reference_path": reference_path,
                                "values": [self.term_to_python(v) for v in ref_values],
                                "evaluation_shape": evaluation_shape,
                            },
                        )
                        continue
                    expected_for_target = self.term_to_python(ref_values[0])
                status = self._compare(actual=actual, operator=node.operator or "", expected=expected_for_target)
                evidence = {
                    "engine": "direct",
                    "node_id": node.node_id,
                    "shape_uri": node.shape_uri,
                    "shape_id": node.shape_id,
                    "target": target,
                    "target_class": node.target_class,
                    "property_path": node.property_path,
                    "operator": node.operator,
                    "expected_value": expected_for_target,
                    "actual_value": actual,
                    "evaluation_shape": evaluation_shape,
                }
                if reference_path:
                    evidence["reference_path"] = reference_path
                if overlay is not None and overlay.has(target, self._written_key(node, prefixes)):
                    evidence["actual_value_source"] = "derived"
                if status:
                    outcomes[target] = self.true_outcome(
                        reason_code="direct_condition_satisfied",
                        message=f"Decision node '{node.node_id}' satisfied for target '{target}'",
                        evidence=evidence,
                    )
                else:
                    outcomes[target] = self.false_outcome(
                        reason_code="direct_condition_violated",
                        message=f"Decision node '{node.node_id}' violated for target '{target}'",
                        evidence=evidence,
                    )
            except Exception as exc:
                outcomes[target] = self.evaluator_error_outcome(
                    node=node,
                    target=target,
                    exc=exc,
                    extra_evidence={
                        "engine": "direct",
                        "graph_id": graph_ref.graph_id,
                        "graph_uri": graph_ref.graph_uri,
                        "rule_id": ctx.rule_id,
                        "evaluation_shape": evaluation_shape,
                    },
                )
        return outcomes

    def evaluate(
        self,
        graph_ref: GraphRef,
        node: DecisionNode,
        target: str,
        ctx: EvalContext,
    ) -> AtomicOutcome:
        try:
            return self.evaluate_many(
                graph_ref=graph_ref,
                node=node,
                targets=[target],
                ctx=ctx,
            )[target]
        except NotImplementedError:
            raise

    @staticmethod
    def _written_key(node: DecisionNode, prefixes: dict[str, str] | None) -> str:
        from app.rules.paths import expand_property_path

        segments = expand_property_path(node.property_path, prefixes)
        return segments[0] if len(segments) == 1 else ""

    def _compare(self, *, actual: Any, operator: str, expected: Any) -> bool:
        op = "==" if operator == "=" else operator

        if op == "between":
            if not isinstance(expected, (list, tuple)) or len(expected) != 2:
                raise NotImplementedError("Direct evaluator requires 2-item list/tuple for 'between'")
            low, high = expected
            actual_num = self._coerce_comparable(actual)
            return self._coerce_comparable(low) <= actual_num < self._coerce_comparable(high)

        if op == "==":
            if isinstance(expected, list):
                actual_norm = self._normalize_value(actual)
                expected_norm = [self._normalize_value(item) for item in expected]
                return actual_norm in expected_norm
            return self._normalize_value(actual) == self._normalize_value(expected)

        if op in {"<", "<=", ">", ">="}:
            actual_num = self._coerce_comparable(actual)
            expected_num = self._coerce_comparable(expected)
            if op == "<":
                return actual_num < expected_num
            if op == "<=":
                return actual_num <= expected_num
            if op == ">":
                return actual_num > expected_num
            return actual_num >= expected_num

        raise NotImplementedError(f"Unsupported direct operator '{operator}'")

    def _coerce_comparable(self, value: Any) -> Any:
        normalized = self._normalize_value(value)
        if isinstance(normalized, Number):
            return normalized
        if isinstance(normalized, bool):
            return normalized
        return str(normalized)

    def _normalize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if text.lower() == "true":
                return True
            if text.lower() == "false":
                return False
            return text
        return value
