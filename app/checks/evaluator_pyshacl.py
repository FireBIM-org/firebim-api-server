from __future__ import annotations

from typing import Any

from pyshacl import validate
from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import SH

from app.checks.evaluator_base import RDFGraphBackedEvaluator
from app.checks.result_models import AtomicOutcome, DecisionNode, EvalContext, GraphRef

FB = Namespace("http://example.org/firebim/rule/")
EX = Namespace("http://example.org/firebim/")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")


class PyShaclAtomicEvaluator(RDFGraphBackedEvaluator):
    def evaluate(
        self,
        graph_ref: GraphRef,
        node: DecisionNode,
        target: str,
        ctx: EvalContext,
    ) -> AtomicOutcome:
        try:
            data_graph = self.get_graph(graph_ref, ctx)

            precheck = self._precheck_unknown(
                data_graph=data_graph,
                graph_ref=graph_ref,
                node=node,
                target=target,
                ctx=ctx,
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

            include_report_text = bool(ctx.extra.get("include_report_text", False))
            include_report_graph_ttl = bool(ctx.extra.get("include_report_graph_ttl", False))

            report_ttl = None
            if include_report_graph_ttl:
                try:
                    report_ttl = report_graph.serialize(format="turtle")
                except Exception:
                    report_ttl = None

            validation_results = []
            if include_report_text or not conforms:
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
            }
            if include_report_text:
                evidence["report_text"] = report_text

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
        graph_ref: GraphRef,
        node: DecisionNode,
        target: str,
        ctx: EvalContext,
    ) -> AtomicOutcome | None:
        if not node.target_class:
            return self.unknown_outcome(
                reason_code="missing_target_class",
                message=f"Decision node '{node.node_id}' has no target class",
                evidence={"node_id": node.node_id, "target": target},
            )

        if not node.property_path:
            return self.unknown_outcome(
                reason_code="missing_property_path",
                message=f"Decision node '{node.node_id}' has no property path",
                evidence={"node_id": node.node_id, "target": target},
            )

        if not node.operator:
            return self.unknown_outcome(
                reason_code="missing_operator",
                message=f"Decision node '{node.node_id}' has no operator",
                evidence={"node_id": node.node_id, "target": target},
            )

        target_ref = URIRef(target)
        target_class_ref = URIRef(node.target_class)

        is_typed = any(data_graph.triples((target_ref, RDF.type, target_class_ref)))
        if not is_typed:
            return self.unknown_outcome(
                reason_code="target_not_instance_of_target_class",
                message=(
                    f"Target '{target}' is not typed as required class '{node.target_class}' for node '{node.node_id}'"
                ),
                evidence={
                    "node_id": node.node_id,
                    "target": target,
                    "target_class": node.target_class,
                },
            )

        values = self.get_property_values(
            graph_ref=graph_ref,
            target=target,
            property_path=node.property_path,
            ctx=ctx,
        )
        if node.operator == "exists":
            present = bool(values)
            return AtomicOutcome(
                status="True" if present else "False",
                reason_code="value_present" if present else "value_absent",
                message=f"Target '{target}' {'has' if present else 'has no'} value for '{node.property_path}'",
                evidence={"engine": "pyshacl", "node_id": node.node_id, "target": target},
            )
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
                    "values": [self.term_to_python(v) for v in values],
                },
            )

        return None

    def _build_atomic_shape_graph(self, *, node: DecisionNode, target: str) -> Graph:
        if not node.shape_uri:
            shape_uri = URIRef(f"http://example.org/firebim/runtime/shape/{node.node_id}")
        else:
            shape_uri = URIRef(node.shape_uri)

        shacl_graph = Graph()
        shacl_graph.bind("sh", SH)
        shacl_graph.bind("fb", FB)
        shacl_graph.bind("ex", EX)

        shape_ref = shape_uri
        target_ref = URIRef(target)
        from rdflib.collection import Collection

        from app.rules.paths import expand_property_path, is_ref_value

        if is_ref_value(node.value):
            raise NotImplementedError("pySHACL evaluator cannot compare with a $ref value; use the direct evaluator")
        segments = expand_property_path(node.property_path)

        def _path_term(seg: str) -> Any:
            if seg.startswith("^"):
                inverse = BNode()
                shacl_graph.add((inverse, SH.inversePath, URIRef(seg[1:])))
                return inverse
            return URIRef(seg)

        if len(segments) == 1:
            property_ref: Any = _path_term(segments[0])
        else:
            # SHACL sequence path = RDF list of (possibly inverse) predicates
            property_ref = BNode()
            Collection(shacl_graph, property_ref, [_path_term(seg) for seg in segments])

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

        if operator == "exists":
            shacl_graph.add((property_shape, SH.minCount, Literal(1)))
            return
        if operator == "<=":
            shacl_graph.add((property_shape, SH.maxInclusive, self._python_to_literal(value)))
            return
        if operator == "<":
            shacl_graph.add((property_shape, SH.maxExclusive, self._python_to_literal(value)))
            return
        if operator == ">=":
            shacl_graph.add((property_shape, SH.minInclusive, self._python_to_literal(value)))
            return
        if operator == ">":
            shacl_graph.add((property_shape, SH.minExclusive, self._python_to_literal(value)))
            return
        if operator in {"=", "=="}:
            if isinstance(value, list):
                shacl_graph.add((property_shape, SH["in"], self._build_rdf_list(shacl_graph, value)))
            else:
                shacl_graph.add((property_shape, SH.hasValue, self._python_to_literal(value)))
            return
        if operator == "between":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError("Operator 'between' requires a 2-item tuple/list value")
            lower, upper = value
            shacl_graph.add((property_shape, SH.minInclusive, self._python_to_literal(lower)))
            shacl_graph.add((property_shape, SH.maxInclusive, self._python_to_literal(upper)))
            return

        raise ValueError(f"Unsupported operator for pySHACL atomic evaluator: '{operator}'")

    def _extract_validation_results_for_target(
        self,
        *,
        report_graph: Graph,
        target: str,
    ) -> list[dict[str, Any]]:
        query = f'''
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
        '''

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

    def _best_report_message(self, validation_results: list[dict[str, Any]]) -> str | None:
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
