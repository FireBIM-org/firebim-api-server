def connect_ruleset_nodes(ruleset: dict, source_id: str, branch: str, target_id: str) -> dict:
    """
    Connect one node branch to another node in a ruleset draft.
    Overwrites existing path if present.
    """

    branch_map = {
        "true": "true_path",
        "false": "false_path",
        "unknown": "unknown_path",
    }

    if branch not in branch_map:
        raise ValueError("branch must be one of: true, false, unknown")

    path_field = branch_map[branch]

    nodes = ruleset.get("nodes", [])
    node_ids = {node.get("id") for node in nodes}

    if source_id not in node_ids:
        raise ValueError(f"Source node '{source_id}' not found")

    if target_id not in node_ids:
        raise ValueError(f"Target node '{target_id}' not found")

    previous_target_id = None
    updated = False
    overwritten = False

    for node in nodes:
        if node.get("id") == source_id:
            previous_target_id = node.get(path_field)

            if previous_target_id is not None and previous_target_id != target_id:
                overwritten = True

            node[path_field] = target_id
            updated = True
            break

    return {
        "updated": updated,
        "overwritten": overwritten,
        "previous_target_id": previous_target_id,
        "ruleset": ruleset,
    }

def disconnect_ruleset_node_branch(ruleset: dict, source_id: str, branch: str) -> dict:
    """
    Remove one branch connection from a node in a ruleset draft.
    """

    branch_map = {
        "true": "true_path",
        "false": "false_path",
        "unknown": "unknown_path",
    }

    if branch not in branch_map:
        raise ValueError("branch must be one of: true, false, unknown")

    path_field = branch_map[branch]

    nodes = ruleset.get("nodes", [])
    node_ids = {node.get("id") for node in nodes}

    if source_id not in node_ids:
        raise ValueError(f"Source node '{source_id}' not found")

    previous_target_id = None
    updated = False

    for node in nodes:
        if node.get("id") == source_id:
            previous_target_id = node.get(path_field)

            if previous_target_id is not None:
                node[path_field] = None
                updated = True

            break

    return {
        "updated": updated,
        "previous_target_id": previous_target_id,
        "ruleset": ruleset,
    }

def check_ruleset_completeness(ruleset: dict) -> dict:
    """
    Check whether a ruleset draft is complete, open, or broken.
    """

    nodes = ruleset.get("nodes", [])
    entry = ruleset.get("entry")

    node_ids = {node.get("id") for node in nodes if node.get("id")}
    missing_nodes = []
    open_branches = []
    errors = []

    entry_ok = entry in node_ids if entry else False
    missing_entry = not bool(entry)

    if missing_entry:
        errors.append("Ruleset is missing an entry node")

    elif not entry_ok:
        errors.append(f"Entry node '{entry}' not found in ruleset")

    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("node_type", "decision")

        # only decision nodes are expected to have all 3 outgoing branches
        if node_type == "decision":
            for branch_name, path_field in [
                ("true", "true_path"),
                ("false", "false_path"),
                ("unknown", "unknown_path"),
            ]:
                target = node.get(path_field)

                if not target:
                    open_branches.append(
                        {
                            "node_id": node_id,
                            "branch": branch_name,
                            "reason": "missing_target",
                        }
                    )
                elif target not in node_ids:
                    missing_nodes.append(target)
                    errors.append(
                        f"Node '{node_id}' has {branch_name}_path -> unknown node '{target}'"
                    )

    # remove duplicates while preserving readable output
    missing_nodes = sorted(set(missing_nodes))

    broken = (not entry_ok) or bool(missing_nodes)
    open_ruleset = bool(open_branches)
    complete = (not broken) and (not open_ruleset)

    return {
        "complete": complete,
        "open": open_ruleset,
        "broken": broken,
        "entry_ok": entry_ok,
        "missing_entry": missing_entry,
        "missing_nodes": missing_nodes,
        "open_branches": open_branches,
        "errors": errors,
    }

def force_end_ruleset(ruleset: dict, mode: str = "per_branch", node_type: str = "end", label_prefix: str = "forced_end") -> dict:
    """
    Fill open branches on decision nodes with generated or shared end nodes.

    Modes:
      - shared: one shared end node for all open branches
      - per_branch: one end node per branch type (true/false/unknown)
      - per_open_path: one unique end node per missing branch
    """

    if mode not in {"shared", "per_branch", "per_open_path"}:
        raise ValueError("mode must be one of: shared, per_branch, per_open_path")

    nodes = ruleset.get("nodes", [])
    existing_ids = {node.get("id") for node in nodes if node.get("id")}
    created_node_ids = []
    filled_count = 0

    def ensure_node(node_id: str):
        nonlocal created_node_ids, existing_ids, nodes
        if node_id not in existing_ids:
            nodes.append({
                "id": node_id,
                "node_type": node_type,
                "label": node_id,
            })
            existing_ids.add(node_id)
            created_node_ids.append(node_id)

    shared_node_id = None
    per_branch_ids = {}

    if mode == "shared":
        shared_node_id = label_prefix
        ensure_node(shared_node_id)

    elif mode == "per_branch":
        for branch in ["true", "false", "unknown"]:
            node_id = f"{label_prefix}_{branch}"
            per_branch_ids[branch] = node_id
            ensure_node(node_id)

    for node in nodes:
        if node.get("node_type", "decision") != "decision":
            continue

        node_id = node.get("id")

        for branch, path_field in [
            ("true", "true_path"),
            ("false", "false_path"),
            ("unknown", "unknown_path"),
        ]:
            if node.get(path_field):
                continue

            if mode == "shared":
                target_id = shared_node_id

            elif mode == "per_branch":
                target_id = per_branch_ids[branch]

            else:  # per_open_path
                target_id = f"{label_prefix}_{node_id}_{branch}"
                ensure_node(target_id)

            node[path_field] = target_id
            filled_count += 1

    return {
        "updated": filled_count > 0,
        "filled_count": filled_count,
        "created_node_ids": created_node_ids,
        "ruleset": ruleset,
    }
