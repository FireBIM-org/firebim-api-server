def validate_rule_element_payload(node: dict) -> None:
    node_id = node.get("id")
    node_type = node.get("node_type", "decision")

    if not node_id:
        raise ValueError("Rule element must have an id")

    if node_type not in {"decision", "start", "end"}:
        raise ValueError("node_type must be one of: decision, start, end")

    if node_type == "decision":
        missing = [
            name
            for name in ["target_class", "property_path", "operator"]
            if not node.get(name)
        ]
        if missing:
            raise ValueError(
                f"Decision node '{node_id}' missing required fields: {', '.join(missing)}"
            )

    elif node_type == "start":
        if node.get("false_path") is not None or node.get("unknown_path") is not None:
            raise ValueError(
                f"Start node '{node_id}' should not define false_path or unknown_path"
            )

    elif node_type == "end":
        if any(
            node.get(k) is not None for k in ["true_path", "false_path", "unknown_path"]
        ):
            raise ValueError(f"End node '{node_id}' should not define outgoing paths")
