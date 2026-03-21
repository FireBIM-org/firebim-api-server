import re
from rdflib import Graph, Namespace, RDF
from rdflib.namespace import SH

EX = Namespace("http://example.org/firebim/")
FB = Namespace("http://example.org/firebim/rule/")


def _local_name(uri) -> str:
    uri = str(uri)
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rstrip("/").rsplit("/", 1)[-1]


def _parse_construct_rule(sparql_text: str):
    prop_match = re.search(r'OPTIONAL\s*\{\s*\?this\s*<([^>]+)>\s*\?h\s*\}', sparql_text, re.DOTALL)
    cmp_match = re.search(r'IF\s*\(\s*\?h\s*(<=|>=|=|<|>)\s*(.+?)\s*,\s*fb:True', sparql_text, re.DOTALL)

    property_path = prop_match.group(1) if prop_match else None
    operator = None
    value = None

    if cmp_match:
        operator = cmp_match.group(1)
        raw_value = cmp_match.group(2).strip().rstrip(") ").strip()

        if re.fullmatch(r"-?\d+", raw_value):
            value = int(raw_value)
        elif re.fullmatch(r"-?\d+\.\d+", raw_value):
            value = float(raw_value)
        elif raw_value.startswith('"') and raw_value.endswith('"'):
            value = raw_value[1:-1]
            if operator == "=":
                operator = "=="
        else:
            value = raw_value
            if operator == "=":
                operator = "=="

    return property_path, operator, value


def decompile_rule_element_ttl(ttl_text: str) -> dict:
    g = Graph()
    g.parse(data=ttl_text, format="turtle")

    # decision first
    shapes = list(g.subjects(RDF.type, SH.NodeShape))
    if shapes:
        shape = shapes[0]
        node_id = _local_name(shape)

        target_obj = g.value(shape, SH.targetClass)
        target_class = _local_name(target_obj) if target_obj else None

        true_obj = g.value(shape, FB.truePath)
        false_obj = g.value(shape, FB.falsePath)
        unknown_obj = g.value(shape, FB.unknownPath)

        rule_bnode = g.value(shape, SH.rule)
        construct_text = g.value(rule_bnode, SH.construct) if rule_bnode else None

        property_path = None
        operator = None
        value = None

        if construct_text:
            property_path, operator, value = _parse_construct_rule(str(construct_text))

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

    # metadata start/end
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

    config = {}
    if end_kind is not None:
        config["end_kind"] = str(end_kind)
    if output_text is not None:
        config["output_text"] = str(output_text)

    return {
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

    nodes = []

    for shape in g.subjects(RDF.type, SH.NodeShape):
        node_id = _local_name(shape)
        target_obj = g.value(shape, SH.targetClass)
        target_class = _local_name(target_obj) if target_obj else None

        true_obj = g.value(shape, FB.truePath)
        false_obj = g.value(shape, FB.falsePath)
        unknown_obj = g.value(shape, FB.unknownPath)

        rule_bnode = g.value(shape, SH.rule)
        construct_text = g.value(rule_bnode, SH.construct) if rule_bnode else None

        property_path = None
        operator = None
        value = None

        if construct_text:
            property_path, operator, value = _parse_construct_rule(str(construct_text))

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

        config = {}
        if end_kind is not None:
            config["end_kind"] = str(end_kind)
        if output_text is not None:
            config["output_text"] = str(output_text)

        nodes.append(
            {
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
        )

    return {
        "id": ruleset_id,
        "entry": entry,
        "nodes": nodes,
    }