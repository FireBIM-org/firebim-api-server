import contextlib
import json

from rdflib import RDF, Graph, Namespace
from rdflib.namespace import SH

from app.rules.construct_parser import parse_construct_rule

EX = Namespace("http://example.org/firebim/")
FB = Namespace("http://example.org/firebim/rule/")


def _local_name(uri) -> str:
    uri = str(uri)
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rstrip("/").rsplit("/", 1)[-1]


def _to_rule_field(uri) -> str | None:
    if uri is None:
        return None
    text = str(uri)
    return text if text.startswith("http://") or text.startswith("https://") else _local_name(uri)


def _parse_construct_rule(sparql_text: str):
    return parse_construct_rule(sparql_text)


def extract_shape_condition(g: Graph, shape) -> tuple[str | None, str | None, object]:
    """(property_path, operator, value) of a decision shape: explicit predicates first, CONSTRUCT regex as fallback."""
    explicit_path = g.value(shape, EX.propertyPath)
    explicit_op = g.value(shape, EX.operator)
    explicit_value = g.value(shape, EX.valueJson)
    if explicit_path is not None and explicit_op is not None:
        value = None
        if explicit_value is not None:
            try:
                value = json.loads(str(explicit_value))
            except ValueError:
                value = str(explicit_value)
        return str(explicit_path), str(explicit_op), value
    rule_bnode = g.value(shape, SH.rule)
    construct_text = g.value(rule_bnode, SH.construct) if rule_bnode else None
    if construct_text:
        return _parse_construct_rule(str(construct_text))
    return None, None, None


def extract_derive_fields(g: Graph, node) -> dict:
    """target_class / property_path / expression / config for an ex:RuleElement with nodeType 'derive'."""
    target_obj = g.value(node, SH.targetClass)
    prop = g.value(node, EX.propertyPath)
    expression = g.value(node, EX.expression)
    prefixes = g.value(node, EX.prefixesJson)
    config: dict = {}
    if prefixes is not None:
        with contextlib.suppress(ValueError):
            config["prefixes"] = json.loads(str(prefixes))
    return {
        "target_class": _to_rule_field(target_obj) if target_obj is not None else None,
        "property_path": str(prop) if prop is not None else None,
        "expression": str(expression) if expression is not None else None,
        "config": config,
    }


def decompile_rule_element_ttl(ttl_text: str) -> dict:
    g = Graph()
    g.parse(data=ttl_text, format="turtle")

    shapes = list(g.subjects(RDF.type, SH.NodeShape))
    if shapes:
        shape = shapes[0]
        node_id = _local_name(shape)

        target_obj = g.value(shape, SH.targetClass)
        target_class = _to_rule_field(target_obj)

        true_obj = g.value(shape, FB.truePath)
        false_obj = g.value(shape, FB.falsePath)
        unknown_obj = g.value(shape, FB.unknownPath)

        property_path, operator, value = extract_shape_condition(g, shape)

        label = g.value(shape, EX.label)

        return {
            "id": node_id,
            "node_type": "decision",
            "target_class": target_class,
            "property_path": property_path,
            "operator": operator,
            "value": value,
            "true_path": _local_name(true_obj) if true_obj else None,
            "false_path": _local_name(false_obj) if false_obj else None,
            "unknown_path": _local_name(unknown_obj) if unknown_obj else None,
            "label": str(label) if label is not None else None,
            "config": {},
        }

    nodes = list(g.subjects(RDF.type, EX.RuleElement))
    if not nodes:
        raise ValueError("No SHACL NodeShape or RuleElement metadata node found in TTL")

    node = nodes[0]
    node_id = _local_name(node)
    node_type = g.value(node, EX.nodeType)
    label = g.value(node, EX.label)
    value = g.value(node, EX.value)

    true_obj = g.value(node, FB.truePath)
    false_obj = g.value(node, FB.falsePath)
    unknown_obj = g.value(node, FB.unknownPath)

    end_kind = g.value(node, EX.endKind)
    output_text = g.value(node, EX.outputText)
    next_rule = g.value(node, EX.nextRule)

    config = {}
    if value is not None:
        config["terminal_value"] = str(value)
    if end_kind is not None:
        config["end_kind"] = str(end_kind)
    if output_text is not None:
        config["output_text"] = str(output_text)
    if next_rule is not None:
        config["next_rule"] = str(next_rule)

    result = {
        "id": node_id,
        "node_type": str(node_type) if node_type is not None else None,
        "target_class": None,
        "property_path": None,
        "operator": None,
        "value": str(value) if value is not None else None,
        "true_path": _local_name(true_obj) if true_obj else None,
        "false_path": _local_name(false_obj) if false_obj else None,
        "unknown_path": _local_name(unknown_obj) if unknown_obj else None,
        "label": str(label) if label is not None else None,
        "config": config,
    }
    if str(node_type) == "derive":
        derive = extract_derive_fields(g, node)
        result.update({k: v for k, v in derive.items() if k != "config"})
        result["value"] = None
        result["config"] = {**config, **derive["config"]}
    return result


def decompile_ruleset_ttl(ttl_text: str) -> dict:
    g = Graph()
    g.parse(data=ttl_text, format="turtle")

    ruleset_subjects = list(g.subjects(RDF.type, EX.RuleSet))
    if not ruleset_subjects:
        raise ValueError("No RuleSet resource found in TTL")

    ruleset_uri = ruleset_subjects[0]
    ruleset_id = _local_name(ruleset_uri)

    entry_obj = g.value(ruleset_uri, EX.entryPoint)
    entry = _local_name(entry_obj) if entry_obj else None
    title_obj = g.value(ruleset_uri, EX.title)
    description_obj = g.value(ruleset_uri, EX.description)
    source_obj = g.value(ruleset_uri, EX.sourceJson)
    source = None
    if source_obj is not None:
        try:
            source = json.loads(str(source_obj))
        except ValueError:
            source = None

    nodes = []

    for shape in g.subjects(RDF.type, SH.NodeShape):
        node_id = _local_name(shape)
        target_obj = g.value(shape, SH.targetClass)
        target_class = _to_rule_field(target_obj)

        true_obj = g.value(shape, FB.truePath)
        false_obj = g.value(shape, FB.falsePath)
        unknown_obj = g.value(shape, FB.unknownPath)

        property_path, operator, value = extract_shape_condition(g, shape)

        label = g.value(shape, EX.label)

        nodes.append(
            {
                "id": node_id,
                "node_type": "decision",
                "target_class": target_class,
                "property_path": property_path,
                "operator": operator,
                "value": value,
                "true_path": _local_name(true_obj) if true_obj else None,
                "false_path": _local_name(false_obj) if false_obj else None,
                "unknown_path": _local_name(unknown_obj) if unknown_obj else None,
                "label": str(label) if label is not None else None,
                "config": {},
            }
        )

    for node in g.subjects(RDF.type, EX.RuleElement):
        node_id = _local_name(node)
        if any(n["id"] == node_id for n in nodes):
            continue

        node_type = g.value(node, EX.nodeType)
        label = g.value(node, EX.label)
        value = g.value(node, EX.value)

        true_obj = g.value(node, FB.truePath)
        false_obj = g.value(node, FB.falsePath)
        unknown_obj = g.value(node, FB.unknownPath)

        end_kind = g.value(node, EX.endKind)
        output_text = g.value(node, EX.outputText)
        next_rule = g.value(node, EX.nextRule)

        config = {}
        if value is not None:
            config["terminal_value"] = str(value)
        if end_kind is not None:
            config["end_kind"] = str(end_kind)
        if output_text is not None:
            config["output_text"] = str(output_text)
        if next_rule is not None:
            config["next_rule"] = str(next_rule)

        entry_dict = {
            "id": node_id,
            "node_type": str(node_type) if node_type is not None else None,
            "target_class": None,
            "property_path": None,
            "operator": None,
            "value": str(value) if value is not None else None,
            "true_path": _local_name(true_obj) if true_obj else None,
            "false_path": _local_name(false_obj) if false_obj else None,
            "unknown_path": _local_name(unknown_obj) if unknown_obj else None,
            "label": str(label) if label is not None else None,
            "config": config,
        }
        if str(node_type) == "derive":
            derive = extract_derive_fields(g, node)
            entry_dict.update({k: v for k, v in derive.items() if k != "config"})
            entry_dict["value"] = None
            entry_dict["config"] = {**config, **derive["config"]}
        nodes.append(entry_dict)

    return {
        "id": ruleset_id,
        "entry": entry,
        "nodes": nodes,
        "title": str(title_obj) if title_obj is not None else None,
        "description": str(description_obj) if description_obj is not None else None,
        "source": source,
    }
