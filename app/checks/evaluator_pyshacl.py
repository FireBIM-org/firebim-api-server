from __future__ import annotations

from typing import Any

from pyshacl import validate
from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import SH

from app.checks.evaluator_base import RDFGraphBackedEvaluator
from app.checks.result_models import AtomicOutcome, DecisionNode, EvalContext, GraphRef

FB = Namespace("http://example.org/firebim/rule/")
EX = Namespace("http://example.org/firebim/")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")


class PyShaclAtomicEvaluator(RDFGraphBackedEvaluator):
    """
    Evaluate one compiled FireBIM decision node against one concrete target
    using pySHACL, one node at a time.

    Evaluation model:
    - build a temporary SHACL graph containing only the current decision shape
    - constrain the shape to the concrete focus node under evaluation
    - run pySHACL validate(data_graph, shacl_graph)
    - map the result into explicit True / False / Unknown

    Important design choice:
    - Unknown is NOT derived from "pySHACL conforms = False"
    - Unknown is decided before validation when required input is missing or
      the decision cannot be safely evaluated from the graph
    - False means the shape was evaluated and violated
    - True means the shape was evaluated and satisfied
    """

    def evaluate(
        self,
        graph_ref: GraphRef,
        node: DecisionNode,
        target: str,
        ctx: EvalContext,
    ) -> AtomicOutcome:
        try:
            data_graph = self.get_graph(graph_ref)

            precheck = self._precheck_unknown(
                data_graph=data_graph, node=node, target=target
            )
            if precheck is not None:
                return precheck

            shacl_graph = self._build_atomic_shape_graph(node=node, target=target)

            conforms, report_graph, report_text = validate(
                data_graph=data_graph,
                shacl_graph=shacl_graph,
                inference=ctx.inference,
                advanced=ctx.advanced,
                debug=ctx.debug,
                serialize_report_graph=False,
            )

            report_ttl = None
            try:
                report_ttl = report_graph.serialize(format="turtle")
            except Exception:
                report_ttl = None

            validation_results = self._extract_validation_results_for_target(
                report_graph=report_graph,
                target=target,
            )

            evidence = {
                "engine": "pyshacl",
                "graph_id": graph_ref.graph_id,
                "graph_uri": graph_ref.graph_uri,
                "rule_id": ctx.rule_id,
                "node_id": node.node_id,
                "shape_uri": node.shape_uri,
                "shape_id": node.shape_id,
                "target": target,
                "target_class": node.target_class,
                "property_path": node.property_path,
                "operator": node.operator,
                "expected_value": node.value,
                "validation_result_count": len(validation_results),
                "validation_results": validation_results,
                "report_text": report_text,
            }

            if conforms:
                return self.true_outcome(
                    reason_code="shape_satisfied",
                    message=f"Decision node '{node.node_id}' satisfied for target '{target}'",
                    evidence=evidence,
                    raw_report_ttl=report_ttl,
                )

            return self.false_outcome(
                reason_code="shape_violated",
                message=self._best_report_message(validation_results)
                or f"Decision node '{node.node_id}' violated for target '{target}'",
                evidence=evidence,
                raw_report_ttl=report_ttl,
            )

        except Exception as exc:
            return self.evaluator_error_outcome(
                node=node,
                target=target,
                exc=exc,
                extra_evidence={
                    "engine": "pyshacl",
                    "graph_id": graph_ref.graph_id,
                    "graph_uri": graph_ref.graph_uri,
                    "rule_id": ctx.rule_id,
                },
            )

    def _precheck_unknown(
        self,
        *,
        data_graph: Graph,
        node: DecisionNode,
        target: str,
    ) -> AtomicOutcome | None:
        """
        Decide explicit Unknown before calling pySHACL when the decision
        is not safely evaluable.

        Current conservative rules:
        - missing target class metadata -> Unknown
        - missing property path metadata -> Unknown
        - missing operator metadata -> Unknown
        - target not typed as target_class -> Unknown
        - no value found for property path on target -> Unknown

        This fits the FireBIM direction where Unknown is explicit and should
        later be routed via unknownPath in runtime traversal.
        """
        if not node.target_class:
            return self.unknown_outcome(
                reason_code="missing_target_class",
                message=f"Decision node '{node.node_id}' has no target class",
                evidence={
                    "node_id": node.node_id,
                    "target": target,
                },
            )

        if not node.property_path:
            return self.unknown_outcome(
                reason_code="missing_property_path",
                message=f"Decision node '{node.node_id}' has no property path",
                evidence={
                    "node_id": node.node_id,
                    "target": target,
                },
            )

        if not node.operator:
            return self.unknown_outcome(
                reason_code="missing_operator",
                message=f"Decision node '{node.node_id}' has no operator",
                evidence={
                    "node_id": node.node_id,
                    "target": target,
                },
            )

        target_ref = URIRef(target)
        target_class_ref = URIRef(node.target_class)
        property_ref = URIRef(node.property_path)

        is_typed = any(data_graph.triples((target_ref, RDF.type, target_class_ref)))
        if not is_typed:
            return self.unknown_outcome(
                reason_code="target_not_instance_of_target_class",
                message=(
                    f"Target '{target}' is not typed as required class "
                    f"'{node.target_class}' for node '{node.node_id}'"
                ),
                evidence={
                    "node_id": node.node_id,
                    "target": target,
                    "target_class": node.target_class,
                },
            )

        values = list(data_graph.objects(target_ref, property_ref))
        if not values:
            return self.unknown_outcome(
                reason_code="missing_required_input",
                message=(
                    f"Target '{target}' has no value for required property "
                    f"'{node.property_path}' in node '{node.node_id}'"
                ),
                evidence={
                    "node_id": node.node_id,
                    "target": target,
                    "target_class": node.target_class,
                    "property_path": node.property_path,
                },
            )

        if len(values) > 1:
            # Conservative for now.
            # Later this may become evaluable for some operators, but for v1
            # it is safer to classify as Unknown/ambiguous than guess.
            return self.unknown_outcome(
                reason_code="ambiguous_input",
                message=(
                    f"Target '{target}' has multiple values for property "
                    f"'{node.property_path}' in node '{node.node_id}'"
                ),
                evidence={
                    "node_id": node.node_id,
                    "target": target,
                    "target_class": node.target_class,
                    "property_path": node.property_path,
                    "values": [self._term_to_python(v) for v in values],
                },
            )

        return None

    def _build_atomic_shape_graph(self, *, node: DecisionNode, target: str) -> Graph:
        """
        Build a temporary SHACL graph containing only the current decision shape,
        rewritten to target exactly one focus node.

        For v1 we model the atomic decision as a simple property shape:
        - sh:targetNode <target>
        - sh:path <property_path>
        - constraint based on operator/value

        This is intentionally pragmatic:
        - runtime remains node-based and explicit
        - pySHACL is used as a validation engine for atomic predicates
        - later we can swap or extend backend without changing runtime contract
        """
        if not node.shape_uri:
            shape_uri = URIRef(
                f"http://example.org/firebim/runtime/shape/{node.node_id}"
            )
        else:
            shape_uri = URIRef(node.shape_uri)

        shacl_graph = Graph()
        shacl_graph.bind("sh", SH)
        shacl_graph.bind("fb", FB)
        shacl_graph.bind("ex", EX)

        shape_ref = shape_uri
        target_ref = URIRef(target)
        property_ref = URIRef(node.property_path)

        shacl_graph.add((shape_ref, RDF.type, SH.NodeShape))
        shacl_graph.add((shape_ref, SH.targetNode, target_ref))

        property_shape = BNode()
        shacl_graph.add((shape_ref, SH.property, property_shape))
        shacl_graph.add((property_shape, SH.path, property_ref))

        self._add_constraint_for_operator(
            shacl_graph=shacl_graph,
            property_shape=property_shape,
            operator=node.operator,
            value=node.value,
        )

        if node.label:
            shacl_graph.add((shape_ref, EX.label, Literal(node.label)))

        return shacl_graph

    def _add_constraint_for_operator(
        self,
        *,
        shacl_graph: Graph,
        property_shape,
        operator: str | None,
        value: Any,
    ) -> None:
        if operator is None:
            raise ValueError("Decision operator is missing")

        if operator == "<=":
            shacl_graph.add(
                (property_shape, SH.maxInclusive, self._python_to_literal(value))
            )
            return

        if operator == "<":
            shacl_graph.add(
                (property_shape, SH.maxExclusive, self._python_to_literal(value))
            )
            return

        if operator == ">=":
            shacl_graph.add(
                (property_shape, SH.minInclusive, self._python_to_literal(value))
            )
            return

        if operator == ">":
            shacl_graph.add(
                (property_shape, SH.minExclusive, self._python_to_literal(value))
            )
            return

        if operator == "=" or operator == "==":
            if isinstance(value, list):
                shacl_graph.add(
                    (property_shape, SH["in"], self._build_rdf_list(shacl_graph, value))
                )
            else:
                shacl_graph.add(
                    (property_shape, SH.hasValue, self._python_to_literal(value))
                )
            return

        if operator == "between":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError(
                    "Operator 'between' requires a 2-item tuple/list value"
                )
            lower, upper = value
            shacl_graph.add(
                (property_shape, SH.minInclusive, self._python_to_literal(lower))
            )
            shacl_graph.add(
                (property_shape, SH.maxInclusive, self._python_to_literal(upper))
            )
            return

        raise ValueError(
            f"Unsupported operator for pySHACL atomic evaluator: '{operator}'"
        )

    def _extract_validation_results_for_target(
        self,
        *,
        report_graph: Graph,
        target: str,
    ) -> list[dict[str, Any]]:
        query = f"""
        PREFIX sh: <{SH}>
        SELECT ?result ?focusNode ?resultPath ?sourceShape ?message ?value
        WHERE {{
          ?result a sh:ValidationResult .
          OPTIONAL {{ ?result sh:focusNode ?focusNode . }}
          OPTIONAL {{ ?result sh:resultPath ?resultPath . }}
          OPTIONAL {{ ?result sh:sourceShape ?sourceShape . }}
          OPTIONAL {{ ?result sh:resultMessage ?message . }}
          OPTIONAL {{ ?result sh:value ?value . }}
          FILTER(!BOUND(?focusNode) || STR(?focusNode) = "{target}")
        }}
        """

        rows: list[dict[str, Any]] = []
        for row in report_graph.query(query):
            rows.append(
                {
                    "result": str(row.result) if row.result else None,
                    "focus_node": str(row.focusNode) if row.focusNode else None,
                    "result_path": str(row.resultPath) if row.resultPath else None,
                    "source_shape": str(row.sourceShape) if row.sourceShape else None,
                    "message": str(row.message) if row.message else None,
                    "value": str(row.value) if row.value else None,
                }
            )
        return rows

    def _best_report_message(
        self, validation_results: list[dict[str, Any]]
    ) -> str | None:
        for item in validation_results:
            msg = item.get("message")
            if msg:
                return str(msg)
        return None

    def _python_to_literal(self, value: Any) -> Literal:
        if isinstance(value, Literal):
            return value
        if isinstance(value, bool):
            return Literal(value)
        if isinstance(value, int):
            return Literal(value)
        if isinstance(value, float):
            return Literal(value)
        return Literal(value)

    def _term_to_python(self, value: Any) -> Any:
        if isinstance(value, Literal):
            return value.toPython()
        return str(value)

    def _build_rdf_list(self, graph: Graph, values: list[Any]):
        if not values:
            return RDF.nil

        head = BNode()
        current = head

        for i, item in enumerate(values):
            graph.add((current, RDF.first, self._python_to_literal(item)))
            if i == len(values) - 1:
                graph.add((current, RDF.rest, RDF.nil))
            else:
                nxt = BNode()
                graph.add((current, RDF.rest, nxt))
                current = nxt

        return head
