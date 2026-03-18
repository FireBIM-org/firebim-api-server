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
    """
    Very first narrow parser for the SPARQL emitted by SHACLBuilder.
    Supports patterns like:

        OPTIONAL { ?this <URI> ?h }
        IF(!BOUND(?h), fb:Unknown,
            IF(?h > 120, fb:True, fb:False)
        )

    Returns:
        property_path, operator, value
    """
    prop_match = re.search(r'OPTIONAL\s*\{\s*\?this\s*<([^>]+)>\s*\?h\s*\}', sparql_text, re.DOTALL)
    cmp_match = re.search(r'IF\s*\(\s*\?h\s*(<=|>=|=|<|>)\s*(.+?)\s*,\s*fb:True', sparql_text, re.DOTALL)

    property_path = prop_match.group(1) if prop_match else None

    operator = None
    value = None

    if cmp_match:
        operator = cmp_match.group(1)
        raw_value = cmp_match.group(2).strip()

        # clean common trailing formatting noise
        raw_value = raw_value.rstrip(") ").strip()

        # try int
        if re.fullmatch(r"-?\d+", raw_value):
            value = int(raw_value)
        # try float
        elif re.fullmatch(r"-?\d+\.\d+", raw_value):
            value = float(raw_value)
        # quoted string
        elif raw_value.startswith('"') and raw_value.endswith('"'):
            value = raw_value[1:-1]
            if operator == "=":
                operator = "=="
        else:
            value = raw_value
            if operator == "=":
                operator = "=="

    return property_path, operator, value


def decompile_ruleset_ttl(ttl_text: str) -> dict:
    g = Graph()
    g.parse(data=ttl_text, format="turtle")

    # Find RuleSet
    ruleset_subjects = list(g.subjects(RDF.type, EX.RuleSet))
    if not ruleset_subjects:
        raise ValueError("No RuleSet resource found in TTL")

    ruleset_uri = ruleset_subjects[0]
    ruleset_id = _local_name(ruleset_uri)

    entry_obj = g.value(ruleset_uri, EX.entryPoint)
    entry = _local_name(entry_obj) if entry_obj else None

    nodes = []

    # decision nodes
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
            }
        )

    # add referenced end/control nodes that are not SHACL shapes
    known_node_ids = {node["id"] for node in nodes}
    referenced_ids = set()

    for node in nodes:
        for key in ["true_path", "false_path", "unknown_path"]:
            if node.get(key):
                referenced_ids.add(node[key])

    for ref_id in sorted(referenced_ids - known_node_ids):
        nodes.append(
            {
                "id": ref_id,
                "node_type": "end",
                "target_class": None,
                "property_path": None,
                "operator": None,
                "value": None,
                "true_path": None,
                "false_path": None,
                "unknown_path": None,
            }
        )

    return {
        "id": ruleset_id,
        "entry": entry,
        "nodes": nodes,
    }

def decompile_rule_element_ttl(ttl_text: str) -> dict:
    g = Graph()
    g.parse(data=ttl_text, format="turtle")

    shapes = list(g.subjects(RDF.type, SH.NodeShape))
    if not shapes:
        raise ValueError("No SHACL NodeShape found in TTL")

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
    }