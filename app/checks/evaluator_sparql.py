from __future__ import annotations

import logging
from typing import Any

import requests

from app.checks.evaluator_base import AtomicEvaluator
from app.checks.result_models import AtomicOutcome, DecisionNode, EvalContext, GraphRef

logger = logging.getLogger(__name__)


class SparqlAtomicEvaluator(AtomicEvaluator):
    """
    Evaluator that translates atomic checks into SPARQL queries against an Ontop endpoint.
    """

    def __init__(self, endpoint_url: str = "http://ontop:8080/sparql"):
        self.endpoint_url = endpoint_url

    def evaluate(
        self,
        graph_ref: GraphRef,
        node: DecisionNode,
        target: str,
        ctx: EvalContext,
    ) -> AtomicOutcome:
        try:
            if not node.target_class:
                return self._error_outcome(node, target, "missing_target_class", "Node has no target class")
            if not node.property_path:
                return self._error_outcome(node, target, "missing_property_path", "Node has no property path")
            if not node.operator:
                return self._error_outcome(node, target, "missing_operator", "Node has no operator")

            query = self._build_query(node, target)
            response = requests.post(
                self.endpoint_url,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
                timeout=30,
            )

            if response.status_code != 200:
                return self._error_outcome(
                    node,
                    target,
                    "sparql_error",
                    f"SPARQL endpoint returned {response.status_code}: {response.text}",
                )

            results = response.json()
            return self._process_results(node, target, results)

        except Exception as exc:
            logger.exception("Error in SparqlAtomicEvaluator")
            return self._error_outcome(node, target, "evaluator_error", str(exc))

    def _build_query(self, node: DecisionNode, target: str) -> str:
        target_uri = target if target.startswith("http") else f"http://example.org/firebim/{target}"
        target_class_uri = (
            node.target_class if node.target_class.startswith("http") else f"http://example.org/fbo/{node.target_class}"
        )
        property_uri = (
            node.property_path
            if node.property_path.startswith("http")
            else f"http://example.org/fbo/{node.property_path}"
        )

        # We use a SELECT query to get the value, then we compare in Python.
        # This allows us to provide better evidence.
        # Alternatively, we could use FILTER in SPARQL for better performance.
        # Given the PRD says "translate rulebook atomic checks into SPARQL queries",
        # maybe I should try to do the comparison in SPARQL.

        query = f"""
        SELECT ?value WHERE {{
          <{target_uri}> a <{target_class_uri}> .
          <{target_uri}> <{property_uri}> ?value .
        }}
        """
        return query

    def _process_results(self, node: DecisionNode, target: str, results: dict[str, Any]) -> AtomicOutcome:
        bindings = results.get("results", {}).get("bindings", [])

        if not bindings:
            # Check if it's the target class that is missing or the property
            # To be precise, we'd need another query, but for now we assume missing required input
            return AtomicOutcome(
                status="Unknown",
                reason_code="missing_required_input",
                message=f"Target '{target}' missing required property or wrong class",
                evidence={
                    "engine": "sparql",
                    "target": target,
                    "property_path": node.property_path,
                },
            )

        if len(bindings) > 1:
            return AtomicOutcome(
                status="Unknown",
                reason_code="ambiguous_input",
                message=f"Target '{target}' has multiple values for '{node.property_path}'",
                evidence={
                    "engine": "sparql",
                    "target": target,
                    "property_path": node.property_path,
                    "values": [b["value"]["value"] for b in bindings],
                },
            )

        actual_value = bindings[0]["value"]["value"]
        datatype = bindings[0]["value"].get("datatype")

        # Basic comparison logic (mimicking DirectAtomicEvaluator)
        status = self._compare(actual_value, node.operator, node.value, datatype)

        evidence = {
            "engine": "sparql",
            "node_id": node.node_id,
            "target": target,
            "property_path": node.property_path,
            "operator": node.operator,
            "expected_value": node.value,
            "actual_value": actual_value,
            "datatype": datatype,
        }

        if status:
            return AtomicOutcome(
                status="True",
                reason_code="condition_satisfied",
                message=f"Condition satisfied for {target}",
                evidence=evidence,
            )
        else:
            return AtomicOutcome(
                status="False",
                reason_code="condition_violated",
                message=f"Condition violated for {target}",
                evidence=evidence,
            )

    def evaluate_many(
        self,
        *,
        graph_ref: GraphRef,
        node: DecisionNode,
        targets: list[str],
        ctx: EvalContext,
    ) -> dict[str, AtomicOutcome]:
        if not targets:
            return {}

        try:
            if not node.target_class:
                return {
                    t: self._error_outcome(node, t, "missing_target_class", "Node has no target class") for t in targets
                }
            if not node.property_path:
                return {
                    t: self._error_outcome(node, t, "missing_property_path", "Node has no property path")
                    for t in targets
                }
            if not node.operator:
                return {t: self._error_outcome(node, t, "missing_operator", "Node has no operator") for t in targets}

            query = self._build_batch_query(node, targets)
            response = requests.post(
                self.endpoint_url,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
                timeout=30,
            )

            if response.status_code != 200:
                error_msg = f"SPARQL endpoint returned {response.status_code}: {response.text}"
                return {t: self._error_outcome(node, t, "sparql_error", error_msg) for t in targets}

            results = response.json()
            return self._process_batch_results(node, targets, results)

        except Exception as exc:
            logger.exception("Error in SparqlAtomicEvaluator.evaluate_many")
            return {t: self._error_outcome(node, t, "evaluator_error", str(exc)) for t in targets}

    def _build_batch_query(self, node: DecisionNode, targets: list[str]) -> str:
        target_class_uri = self._expand_uri(node.target_class, "fbo")
        property_uri = self._expand_uri(node.property_path, "fbo")

        target_uris = [self._expand_uri(t, "firebim") for t in targets]
        values_clause = " ".join([f"<{uri}>" for uri in target_uris])

        query = f"""
        SELECT ?target ?value WHERE {{
          VALUES ?target {{ {values_clause} }}
          ?target a <{target_class_uri}> .
          ?target <{property_uri}> ?value .
        }}
        """
        return query

    def _process_batch_results(
        self, node: DecisionNode, targets: list[str], results: dict[str, Any]
    ) -> dict[str, AtomicOutcome]:
        bindings = results.get("results", {}).get("bindings", [])

        # Group by target
        target_to_values: dict[str, list[dict[str, Any]]] = {}
        for b in bindings:
            target_uri = b["target"]["value"]
            target_to_values.setdefault(target_uri, []).append(b["value"])

        outcomes: dict[str, AtomicOutcome] = {}
        for target in targets:
            target_uri = self._expand_uri(target, "firebim")
            values = target_to_values.get(target_uri, [])

            if not values:
                outcomes[target] = AtomicOutcome(
                    status="Unknown",
                    reason_code="missing_required_input",
                    message=f"Target '{target}' missing required property or wrong class",
                    evidence={
                        "engine": "sparql",
                        "target": target,
                        "property_path": node.property_path,
                    },
                )
                continue

            if len(values) > 1:
                outcomes[target] = AtomicOutcome(
                    status="Unknown",
                    reason_code="ambiguous_input",
                    message=f"Target '{target}' has multiple values for '{node.property_path}'",
                    evidence={
                        "engine": "sparql",
                        "target": target,
                        "property_path": node.property_path,
                        "values": [v["value"] for v in values],
                    },
                )
                continue

            val_obj = values[0]
            actual_value = val_obj["value"]
            datatype = val_obj.get("datatype")

            status = self._compare(actual_value, node.operator, node.value, datatype)

            evidence = {
                "engine": "sparql",
                "node_id": node.node_id,
                "target": target,
                "property_path": node.property_path,
                "operator": node.operator,
                "expected_value": node.value,
                "actual_value": actual_value,
                "datatype": datatype,
            }

            if status:
                outcomes[target] = AtomicOutcome(
                    status="True",
                    reason_code="condition_satisfied",
                    message=f"Condition satisfied for {target}",
                    evidence=evidence,
                )
            else:
                outcomes[target] = AtomicOutcome(
                    status="False",
                    reason_code="condition_violated",
                    message=f"Condition violated for {target}",
                    evidence=evidence,
                )

        return outcomes

    def _expand_uri(self, uri: str | None, default_ns: str) -> str:
        if not uri:
            return ""
        if uri.startswith("http"):
            return uri
        if default_ns == "fbo":
            return f"http://example.org/fbo/{uri}"
        if default_ns == "firebim":
            return f"http://example.org/firebim/{uri}"
        return uri

    def _compare(self, actual: str, operator: str, expected: Any, datatype: str | None) -> bool:
        # Coerce actual value based on datatype
        coerced_actual: Any = actual
        if datatype == "http://www.w3.org/2001/XMLSchema#integer":
            coerced_actual = int(actual)
        elif (
            datatype == "http://www.w3.org/2001/XMLSchema#decimal"
            or datatype == "http://www.w3.org/2001/XMLSchema#float"
            or datatype == "http://www.w3.org/2001/XMLSchema#double"
        ):
            coerced_actual = float(actual)
        elif datatype == "http://www.w3.org/2001/XMLSchema#boolean":
            coerced_actual = actual.lower() == "true"

        # Handle comparisons
        if operator in {"=", "=="}:
            return str(coerced_actual) == str(expected)
        if operator == ">":
            return coerced_actual > expected
        if operator == ">=":
            return coerced_actual >= expected
        if operator == "<":
            return coerced_actual < expected
        if operator == "<=":
            return coerced_actual <= expected
        if operator == "between":
            low, high = expected
            return low <= coerced_actual < high

        return False

    def _error_outcome(self, node: DecisionNode, target: str, reason: str, message: str) -> AtomicOutcome:
        return AtomicOutcome(
            status="Unknown",
            reason_code=reason,
            message=message,
            evidence={
                "engine": "sparql",
                "node_id": node.node_id,
                "target": target,
            },
        )
