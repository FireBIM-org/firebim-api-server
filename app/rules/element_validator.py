from app.rules.expressions import ExpressionError, validate_expression
from app.rules.paths import expand_curie, is_ref_value, ref_path, split_property_path, validate_path_syntax

ELEMENT_TYPES = {"decision", "derive", "start", "end"}
OPERATORS = {">", "<", "<=", ">=", "between", "==", "exists"}


def validate_rule_element_payload(node: dict) -> None:
    node_id = node.get("id")
    node_type = node.get("node_type") or node.get("element_type") or "decision"

    if not node_id:
        raise ValueError("Rule element must have an id")

    if node_type not in ELEMENT_TYPES:
        raise ValueError("node_type must be one of: decision, derive, start, end")

    if node_type == "decision":
        missing = [name for name in ["target_class", "property_path", "operator"] if not node.get(name)]
        if missing:
            raise ValueError(f"Decision node '{node_id}' missing required fields: {', '.join(missing)}")
        validate_path_syntax(node.get("property_path"))
        if node.get("expression"):
            raise ValueError(f"Decision node '{node_id}' must not define an expression (use a derive element)")

        operator = node.get("operator")
        value = node.get("value")
        if operator == "exists":
            # presence check: True when the path has at least one value, False otherwise (never Unknown)
            if value not in (None, True, "*", "true"):
                raise ValueError(f"Decision node '{node_id}' with operator 'exists' takes no value")
        elif operator in OPERATORS:
            if value is None:
                raise ValueError(f"Decision node '{node_id}' with operator '{operator}' must define a value")

            if is_ref_value(value):
                path = ref_path(value)
                if not path:
                    raise ValueError(f"Decision node '{node_id}' has an empty $ref")
                validate_path_syntax(path, field="value.$ref")
                if operator == "between":
                    raise ValueError(f"Decision node '{node_id}': 'between' cannot be combined with a $ref value")
            elif isinstance(value, dict):
                raise ValueError(f"Decision node '{node_id}' value must be a literal, a list, or {{\"$ref\": path}}")
            elif operator == "between" and (not isinstance(value, (list, tuple)) or len(value) != 2):
                raise ValueError(
                    f"Decision node '{node_id}' with operator 'between' must define "
                    "value as a list or tuple of length 2"
                )

    elif node_type == "derive":
        missing = [name for name in ["target_class", "property_path", "expression"] if not node.get(name)]
        if missing:
            raise ValueError(f"Derive node '{node_id}' missing required fields: {', '.join(missing)}")
        validate_path_syntax(node.get("property_path"))
        if len(split_property_path(node.get("property_path"))) != 1:
            raise ValueError(f"Derive node '{node_id}' must write exactly one property (no sequence path)")
        forbidden = [name for name in ["operator", "value", "false_path"] if node.get(name) is not None]
        if forbidden:
            raise ValueError(f"Derive node '{node_id}' must not define: {', '.join(forbidden)}")
        # true_path is required in a *rule* (lint E-derive-dangling), not on a library element that is not connected yet
        try:
            refs = validate_expression(str(node.get("expression")))
        except ExpressionError as exc:
            raise ValueError(f"Derive node '{node_id}' expression is invalid: {exc}") from exc
        prefixes = (node.get("config") or {}).get("prefixes") if isinstance(node.get("config"), dict) else None
        written = expand_curie(str(node.get("property_path")), prefixes)
        for ref in refs:
            if expand_curie(ref, prefixes) == written:
                raise ValueError(
                    f"Derive node '{node_id}' expression reads the property it writes ({node.get('property_path')})"
                )

    elif node_type == "start":
        if node.get("false_path") is not None or node.get("unknown_path") is not None:
            raise ValueError(f"Start node '{node_id}' should not define false_path or unknown_path")

    elif node_type == "end":
        if any(node.get(k) is not None for k in ["true_path", "false_path", "unknown_path"]):
            raise ValueError(f"End node '{node_id}' should not define outgoing paths")
