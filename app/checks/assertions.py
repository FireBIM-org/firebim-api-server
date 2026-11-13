"""User answers for a building graph.

Two halves:

* `apply_assertions(ttl, assertions)` writes facts a person supplied into a building graph — either for one run
  (`options.overrides`) or persistently, as an *answered copy* of the graph (`POST /graphs/{id}/answers`).
* `open_questions(...)` turns the Unknown outcomes of a run into concrete questions a UI can ask: which subject,
  which property, what kind of answer (yes/no, one of these codes, a number), so that the answer can be written
  back with `apply_assertions` and the rule re-run.

The rule engine never guesses; this is the place where the person supplies what the model does not say.
"""

from __future__ import annotations

from typing import Any

from rdflib import XSD, Graph, Literal, URIRef

from app.checks.derived import resolve_path_values, to_python
from app.rules.paths import expand_property_path, is_ref_value, ref_path

FBO = "https://firebim.org/ontologies/fbo#"
ANSWERED_FROM = URIRef(FBO + "answeredFromGraph")
ANSWER_SUFFIX = "--answered"


def answered_graph_id(graph_id: str) -> str:
    """The id of the answered copy of a graph (answering again writes into the same copy)."""
    return graph_id if graph_id.endswith(ANSWER_SUFFIX) else f"{graph_id}{ANSWER_SUFFIX}"


def _literal(value: Any, datatype: str | None) -> Literal | URIRef:
    if isinstance(value, dict) and "iri" in value:
        return URIRef(str(value["iri"]))
    if datatype in ("iri", "uri", "resource"):
        return URIRef(str(value))
    if datatype:
        return Literal(value, datatype=URIRef(datatype) if datatype.startswith("http") else getattr(XSD, datatype))
    if isinstance(value, bool):
        return Literal(value)
    if isinstance(value, int):
        return Literal(value)
    if isinstance(value, float):
        return Literal(value, datatype=XSD.decimal)
    text = str(value).strip()
    if text.lower() in ("true", "false"):
        return Literal(text.lower() == "true")
    try:
        if text.lstrip("-").isdigit():
            return Literal(int(text))
        return Literal(float(text), datatype=XSD.decimal)
    except ValueError:
        return Literal(text)


def apply_assertions(
    graph_ttl: str, assertions: list[Any], *, provenance: tuple[str, str] | None = None
) -> tuple[str, int]:
    """Write `assertions` ({subject, property, value, datatype?, replace?}) into the graph. Returns (ttl, applied).

    `provenance=(answered_graph_uri, base_graph_uri)` records that the copy was answered from the base graph."""
    graph = Graph()
    graph.parse(data=graph_ttl, format="turtle")
    applied = 0
    for item in assertions:
        data = item if isinstance(item, dict) else item.model_dump()
        subject = URIRef(str(data["subject"]))
        prop = URIRef(str(data["property"]))
        if data.get("replace", True):
            graph.remove((subject, prop, None))
        graph.add((subject, prop, _literal(data.get("value"), data.get("datatype"))))
        applied += 1
    if provenance:
        answered_uri, base_uri = provenance
        graph.add((URIRef(answered_uri), ANSWERED_FROM, URIRef(base_uri)))
    return graph.serialize(format="turtle"), applied


# ------------------------------------------------------------------------------------------------- questions
_NUMERIC_OPERATORS = {"<", "<=", ">", ">=", "between"}


def _kind_and_options(operator: str | None, value: Any, sibling_values: list[Any]) -> tuple[str, list[Any]]:
    if operator in _NUMERIC_OPERATORS:
        return "number", []
    if isinstance(value, bool):
        return "boolean", [True, False]
    if isinstance(value, (int, float)):
        return "number", []
    options: list[Any] = []
    for candidate in ([*value] if isinstance(value, list) else [value]) + sibling_values:
        if candidate is None or isinstance(candidate, dict) or candidate in options:
            continue
        options.append(candidate)
    return ("choice", options) if options else ("text", [])


def _write_target(graph: Graph, target: str, segments: list[str]) -> tuple[str | None, str | None, str | None]:
    """(subject, property, problem) where the answer has to be written for a (possibly multi-hop) path."""
    if not segments:
        return None, None, "no property path"
    last = segments[-1]
    if last.startswith("^"):
        return None, None, "the last step of the path is inverse; the fact belongs on the other node"
    if len(segments) == 1:
        return target, last, None
    subjects = [str(v) for v in resolve_path_values(graph, target, segments[:-1]) if isinstance(v, URIRef)]
    if len(subjects) == 1:
        return subjects[0], last, None
    prefix = "/".join(segments[:-1])
    if not subjects:
        if any(seg.rsplit("#", 1)[-1].rsplit("/", 1)[-1] in ("partOf", "isPartOf") for seg in segments[:-1]):
            return (
                None,
                last,
                "this target is not part of any unit in the graph (no node has fbo:hasSubitem pointing at it), so the "
                "unit's fact cannot be read or answered; add the containing bygningsafsnit / compartment to the model "
                "(the question is about the unit, not about this target)",
            )
        return (
            None,
            last,
            f"the graph does not link this target to anything through {prefix}; the fact belongs on the node at the "
            "end of that path, which does not exist yet",
        )
    return None, last, f"the path {prefix} leads to {len(subjects)} nodes; answer on one of them"


def open_questions(*, result: Any, graph_ttl: str, compiled: Any) -> list[dict[str, Any]]:
    """One question per (target, missing fact) for every target whose last step ended Unknown."""
    graph = Graph()
    graph.parse(data=graph_ttl, format="turtle")
    nodes = getattr(compiled, "nodes", {}) or {}

    sibling_literals: dict[str, list[Any]] = {}
    for node in nodes.values():
        if getattr(node, "node_type", None) != "decision" or not getattr(node, "property_path", None):
            continue
        value = getattr(node, "value", None)
        if value is None or is_ref_value(value):
            continue
        bucket = sibling_literals.setdefault(str(node.property_path), [])
        for item in value if isinstance(value, list) else [value]:
            if item not in bucket:
                bucket.append(item)

    questions: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for target in getattr(result, "targets", []) or []:
        if getattr(target, "final_status", None) != "Unknown":
            continue
        step = next(
            (
                s
                for s in reversed(target.trace or [])
                if s.status == "Unknown" and s.node_type in ("decision", "derive")
            ),
            None,
        )
        if step is None:
            continue
        node = nodes.get(step.node_id)
        evidence = step.evidence or {}
        reason = str(evidence.get("reason_code") or "")
        if reason == "target_not_instance_of_target_class":
            continue

        operator = getattr(node, "operator", None) if node is not None else evidence.get("operator")
        value = getattr(node, "value", None) if node is not None else evidence.get("expected_value")
        node_type = step.node_type
        label = getattr(node, "label", None) if node is not None else None

        if node_type == "derive":
            path = evidence.get("property_path")
            written = getattr(node, "property_path", None) if node is not None else None
            if not path or path == written:
                continue  # an expression error, not a missing fact
            operator, value = None, None
            kind, options = "number", []
        elif reason in ("missing_reference_input", "ambiguous_reference_input"):
            path = evidence.get("reference_path") or (ref_path(value) if is_ref_value(value) else None)
            if not path:
                continue
            kind, options = "text", []
            if reason == "ambiguous_reference_input":
                kind, options = "choice", list(evidence.get("values") or [])
            operator, value = "==", None
        else:
            path = getattr(node, "property_path", None) if node is not None else evidence.get("property_path")
            if not path:
                continue
            kind, options = _kind_and_options(operator, value, sibling_literals.get(str(path), []))
            if reason == "ambiguous_input" and evidence.get("values"):
                kind = "choice"
                options = [v for v in evidence["values"] if v not in options] + options

        segments = expand_property_path(str(path))
        subject, prop, problem = _write_target(graph, target.target_node, segments)
        key = (target.target_node, subject, prop)
        if key in seen:
            continue
        seen.add(key)
        current = evidence.get("values") or evidence.get("actual_value")
        questions.append(
            {
                "target_node": target.target_node,
                "node_id": step.node_id,
                "node_type": node_type,
                "label": label,
                "property_path": str(path),
                "operator": operator,
                "value": value,
                "reason_code": reason or None,
                "message": step.evidence.get("message") or getattr(target, "message", None),
                "kind": kind,
                "options": [to_python(o) if not isinstance(o, (str, int, float, bool)) else o for o in options],
                "current_value": current,
                "write_subject": subject,
                "write_property": prop,
                "problem": problem,
            }
        )
    return questions
