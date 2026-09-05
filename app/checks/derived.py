"""Derived-value overlay and the evaluator for `derive` rule elements.

The overlay is created per rule run. `derive` elements write into it; every evaluator reads the overlay
*before* the base graph, so a derived value shadows whatever the base graph holds for that (target, property).
Nothing is ever written into the base graph.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef

from app.rules.expressions import ExpressionUnknown, Node, describe, evaluate, parse_expression
from app.rules.paths import expand_curie, expand_property_path, is_inverse_segment, segment_iri

PROV = Namespace("http://www.w3.org/ns/prov#")
FBO = Namespace("https://firebim.org/ontologies/fbo#")
DERIVED_GRAPH_BASE_URI = "https://firebim.org/graphs/derived/"


def default_derived_graph_uri(graph_id: str, rule_id: str) -> str:
    return f"{DERIVED_GRAPH_BASE_URI}{str(graph_id).strip()}/{str(rule_id).strip()}"


def to_python(value: Any) -> Any:
    if isinstance(value, Literal):
        py = value.toPython()
        if isinstance(py, Literal):  # unknown datatype
            return str(value)
        if isinstance(py, Decimal):
            return int(py) if py == py.to_integral_value() else float(py)
        return py
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, URIRef):
        return str(value)
    return value


def to_literal(value: Any) -> Literal | URIRef:
    if isinstance(value, Literal | URIRef):
        return value
    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    if isinstance(value, int):
        return Literal(value, datatype=XSD.integer)
    if isinstance(value, float):
        return Literal(value, datatype=XSD.double)
    if isinstance(value, str) and value.startswith(("http://", "https://", "urn:")):
        return URIRef(value)
    return Literal(str(value))


class DerivedOverlay:
    """Per-run store of derived (target, property) -> value, with provenance."""

    def __init__(self, *, rule_id: str, graph_id: str, base_graph_hash: str | None = None) -> None:
        self.rule_id = rule_id
        self.graph_id = graph_id
        self.base_graph_hash = base_graph_hash
        self.values: dict[tuple[str, str], Any] = {}
        self.records: list[dict[str, Any]] = []
        self.written_properties: set[str] = set()
        self.graph = Graph()
        self.graph.bind("prov", PROV)
        self.graph.bind("fbo", FBO)
        self.created_at = datetime.now(UTC).isoformat()

    # -- reads -----------------------------------------------------------------------------------
    def has(self, target: str, prop: str) -> bool:
        return (target, prop) in self.values

    def get(self, target: str, prop: str) -> Any:
        return self.values[(target, prop)]

    def shadows(self, prop: str) -> bool:
        return prop in self.written_properties

    # -- writes ----------------------------------------------------------------------------------
    def write(
        self,
        *,
        target: str,
        prop: str,
        value: Any,
        node_id: str,
        inputs: dict[str, Any] | None = None,
        expression: str | None = None,
    ) -> dict[str, Any]:
        key = (target, prop)
        previous = self.values.get(key, _MISSING)
        self.values[key] = value
        self.written_properties.add(prop)
        target_ref, prop_ref = URIRef(target), URIRef(prop)
        self.graph.remove((target_ref, prop_ref, None))
        self.graph.add((target_ref, prop_ref, to_literal(value)))
        record = {
            "target": target,
            "property": prop,
            "value": value,
            "node_id": node_id,
            "expression": expression,
            "inputs": inputs or {},
        }
        if previous is not _MISSING:
            record["overwrote_previous"] = previous
        self.records.append(record)
        return record

    # -- export ----------------------------------------------------------------------------------
    def to_ttl(self, *, run_uri: str | None = None) -> str:
        g = Graph()
        g.bind("prov", PROV)
        g.bind("fbo", FBO)
        for triple in self.graph:
            g.add(triple)
        run = URIRef(run_uri or f"https://firebim.org/check-runs/{self.rule_id}/{self.created_at}")
        g.add((run, RDF.type, PROV.Activity))
        g.add((run, FBO.ruleId, Literal(self.rule_id)))
        g.add((run, FBO.graphId, Literal(self.graph_id)))
        g.add((run, PROV.endedAtTime, Literal(self.created_at, datatype=XSD.dateTime)))
        if self.base_graph_hash:
            g.add((run, FBO.baseGraphHash, Literal(self.base_graph_hash)))
        for record in self.records:
            g.add((URIRef(record["target"]), PROV.wasGeneratedBy, run))
        return g.serialize(format="turtle")


_MISSING = object()


# ------------------------------------------------------------------------------------------------
class PathUnknown(Exception):
    def __init__(self, reason_code: str, message: str, **evidence: Any) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message
        self.evidence = evidence


def resolve_path_values(
    graph: Graph,
    target: str,
    segments: list[str],
    overlay: DerivedOverlay | None = None,
) -> list[Any]:
    """All *distinct* values reachable from `target` along the sequence path (rdflib terms), overlay-aware per hop.

    A segment starting with ``^`` is followed backwards (subjects that point at the current node).

    Duplicates are collapsed at every hop, so a sequence path that reaches the same literal through several
    intermediate nodes (unit -> compartments -> UseCategory = "AK2", "AK2") yields one value; only genuinely
    different values are reported as ambiguous by the evaluators.
    """
    frontier: list[Any] = [URIRef(target)]
    for prop in segments:
        nxt: list[Any] = []
        seen: set[Any] = set()
        for node in frontier:
            if not isinstance(node, URIRef):
                continue
            if is_inverse_segment(prop):
                candidates: list[Any] = list(graph.subjects(URIRef(segment_iri(prop)), node))
            elif overlay is not None and overlay.has(str(node), prop):
                candidates = [to_literal(overlay.get(str(node), prop))]
            else:
                candidates = list(graph.objects(node, URIRef(prop)))
            for value in candidates:
                if value in seen:
                    continue
                seen.add(value)
                nxt.append(value)
        frontier = nxt
        if not frontier:
            break
    return frontier


def resolve_single(
    graph: Graph,
    target: str,
    path: str,
    overlay: DerivedOverlay | None,
    prefixes: dict[str, str] | None = None,
) -> Any:
    segments = expand_property_path(path, prefixes)
    values = resolve_path_values(graph, target, segments, overlay)
    if not values:
        raise PathUnknown("missing_required_input", f"no value for {path} on {target}", property_path=path)
    if len(values) > 1:
        raise PathUnknown(
            "ambiguous_input",
            f"{len(values)} values for {path} on {target}",
            property_path=path,
            values=[to_python(v) for v in values],
        )
    return to_python(values[0])


# ------------------------------------------------------------------------------------------------
class DeriveEvaluator:
    """Evaluates a derive node for one target and writes the result to the overlay."""

    def __init__(self) -> None:
        self._ast_cache: dict[str, Node] = {}

    def ast(self, expression: str) -> Node:
        node = self._ast_cache.get(expression)
        if node is None:
            node = parse_expression(expression)
            self._ast_cache[expression] = node
        return node

    def run(
        self,
        *,
        graph: Graph,
        overlay: DerivedOverlay,
        target: str,
        node_id: str,
        target_class: str | None,
        property_path: str,
        expression: str,
        prefixes: dict[str, str] | None = None,
    ) -> tuple[str, str | None, str | None, dict[str, Any]]:
        """-> (status, reason_code, message, evidence). status is "True" (written) or "Unknown"."""
        evidence: dict[str, Any] = {
            "engine": "derive",
            "node_id": node_id,
            "target": target,
            "target_class": target_class,
            "property_path": property_path,
            "expression": expression,
        }
        if target_class and not any(graph.triples((URIRef(target), RDF.type, URIRef(target_class)))):
            return (
                "Unknown",
                "target_not_instance_of_target_class",
                f"Target '{target}' is not typed as '{target_class}' for derive node '{node_id}'",
                evidence,
            )
        inputs: dict[str, Any] = {}

        def resolve(path: str) -> Any:
            try:
                value = resolve_single(graph, target, path, overlay, prefixes)
            except PathUnknown as exc:
                raise ExpressionUnknown(exc.reason_code, exc.message, **exc.evidence) from exc
            inputs[path] = value
            return value

        try:
            ast = self.ast(expression)
            value = evaluate(ast, resolve)
        except ExpressionUnknown as exc:
            evidence.update({"inputs": inputs, **exc.evidence})
            return "Unknown", exc.reason_code, f"Derive node '{node_id}': {exc.message}", evidence
        except Exception as exc:  # parser errors slipped through validation
            evidence.update({"inputs": inputs})
            return "Unknown", "expression_error", f"Derive node '{node_id}': {exc}", evidence
        if value is None:
            evidence.update({"inputs": inputs})
            return "Unknown", "expression_error", f"Derive node '{node_id}' evaluated to null", evidence
        written_prop = expand_curie(property_path, prefixes)
        record = overlay.write(
            target=target, prop=written_prop, value=value, node_id=node_id, inputs=inputs, expression=expression
        )
        base_values = [to_python(v) for v in graph.objects(URIRef(target), URIRef(written_prop))]
        evidence.update({"inputs": inputs, "derived_value": value, "normalized_expression": describe(ast)})
        if base_values:
            evidence["shadowed_base_values"] = base_values
        if "overwrote_previous" in record:
            evidence["overwrote_previous"] = record["overwrote_previous"]
        return "True", "derived_value_written", f"Derive node '{node_id}' wrote {property_path} = {value!r}", evidence
