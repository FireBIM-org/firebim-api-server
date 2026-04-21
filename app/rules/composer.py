from typing import Literal, TypedDict


class RuleElementDict(TypedDict, total=False):
    id: str
    element_type: str
    node_type: str
    label: str
    true_path: str | None
    false_path: str | None
    unknown_path: str | None


class RuleSetDict(TypedDict, total=False):
    id: str
    entry: str
    elements: list[RuleElementDict]
    nodes: list[RuleElementDict]


class OpenBranchInfo(TypedDict):
    node_id: str | None
    branch: str
    reason: str


class ConnectRulesetResult(TypedDict):
    updated: bool
    overwritten: bool
    previous_target_id: str | None
    ruleset: RuleSetDict


class DisconnectRulesetResult(TypedDict):
    updated: bool
    previous_target_id: str | None
    ruleset: RuleSetDict


class RulesetCompletenessResult(TypedDict):
    complete: bool
    open: bool
    broken: bool
    entry_ok: bool
    missing_entry: bool
    missing_nodes: list[str]
    open_branches: list[OpenBranchInfo]
    errors: list[str]


class ForceEndRulesetResult(TypedDict):
    updated: bool
    filled_count: int
    created_node_ids: list[str]
    ruleset: RuleSetDict


def _get_elements(ruleset: RuleSetDict) -> list[RuleElementDict]:
    elements = ruleset.get("elements")
    if elements is None:
        elements = ruleset.get("nodes", [])
    return elements


def _sync_ruleset_containers(ruleset: RuleSetDict, elements: list[RuleElementDict]) -> RuleSetDict:
    ruleset["elements"] = elements
    ruleset["nodes"] = elements
    return ruleset


def connect_ruleset_nodes(
    ruleset: RuleSetDict,
    source_id: str,
    branch: Literal["true", "false", "unknown"],
    target_id: str,
) -> ConnectRulesetResult:
    """
    Connect one rule element branch to another rule element in a draft Rule.
    Accepts both legacy nodes[] and new elements[] payloads.
    """

    branch_map = {
        "true": "true_path",
        "false": "false_path",
        "unknown": "unknown_path",
    }

    if branch not in branch_map:
        raise ValueError("branch must be one of: true, false, unknown")

    path_field = branch_map[branch]
    elements = _get_elements(ruleset)
    element_ids = {element.get("id") for element in elements}

    if source_id not in element_ids:
        raise ValueError(f"Source rule element '{source_id}' not found")

    if target_id not in element_ids:
        raise ValueError(f"Target rule element '{target_id}' not found")

    previous_target_id = None
    updated = False
    overwritten = False

    for element in elements:
        if element.get("id") == source_id:
            previous_target_id = element.get(path_field)

            if previous_target_id is not None and previous_target_id != target_id:
                overwritten = True

            element[path_field] = target_id
            updated = True
            break

    return {
        "updated": updated,
        "overwritten": overwritten,
        "previous_target_id": previous_target_id,
        "ruleset": _sync_ruleset_containers(ruleset, elements),
    }


def disconnect_ruleset_node_branch(
    ruleset: RuleSetDict,
    source_id: str,
    branch: Literal["true", "false", "unknown"],
) -> DisconnectRulesetResult:
    """
    Remove one branch connection from a rule element in a draft Rule.
    """

    branch_map = {
        "true": "true_path",
        "false": "false_path",
        "unknown": "unknown_path",
    }

    if branch not in branch_map:
        raise ValueError("branch must be one of: true, false, unknown")

    path_field = branch_map[branch]
    elements = _get_elements(ruleset)
    element_ids = {element.get("id") for element in elements}

    if source_id not in element_ids:
        raise ValueError(f"Source rule element '{source_id}' not found")

    previous_target_id = None
    updated = False

    for element in elements:
        if element.get("id") == source_id:
            previous_target_id = element.get(path_field)

            if previous_target_id is not None:
                element[path_field] = None
                updated = True

            break

    return {
        "updated": updated,
        "previous_target_id": previous_target_id,
        "ruleset": _sync_ruleset_containers(ruleset, elements),
    }


def check_ruleset_completeness(ruleset: RuleSetDict) -> RulesetCompletenessResult:
    """
    Check whether a draft Rule is complete, open, or broken.
    """

    elements = _get_elements(ruleset)
    entry = ruleset.get("entry")

    element_ids = {element.get("id") for element in elements if element.get("id")}
    missing_nodes = []
    open_branches = []
    errors = []

    entry_ok = entry in element_ids if entry else False
    missing_entry = not bool(entry)

    if missing_entry:
        errors.append("Rule is missing an entry element")
    elif not entry_ok:
        errors.append(f"Entry element '{entry}' not found in rule")

    for element in elements:
        element_id = element.get("id")
        element_type = element.get("element_type") or element.get("node_type", "decision")

        if element_type == "decision":
            for branch_name, path_field in [
                ("true", "true_path"),
                ("false", "false_path"),
                ("unknown", "unknown_path"),
            ]:
                target = element.get(path_field)

                if not target:
                    open_branches.append(
                        {
                            "node_id": element_id,
                            "branch": branch_name,
                            "reason": "missing_target",
                        }
                    )
                elif target not in element_ids:
                    missing_nodes.append(target)
                    errors.append(
                        f"Rule element '{element_id}' has {branch_name}_path -> unknown rule element '{target}'"
                    )

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


def force_end_ruleset(
    ruleset: RuleSetDict,
    mode: Literal["shared", "per_branch", "per_open_path"] = "per_branch",
    node_type: str = "end",
    label_prefix: str = "forced_end",
) -> ForceEndRulesetResult:
    """
    Fill open branches on decision rule elements with generated or shared end rule elements.
    """

    if mode not in {"shared", "per_branch", "per_open_path"}:
        raise ValueError("mode must be one of: shared, per_branch, per_open_path")

    elements = _get_elements(ruleset)
    existing_ids = {element.get("id") for element in elements if element.get("id")}
    created_node_ids = []
    filled_count = 0

    def ensure_element(element_id: str) -> None:
        nonlocal created_node_ids, existing_ids, elements
        if element_id not in existing_ids:
            elements.append(
                {
                    "id": element_id,
                    "element_type": node_type,
                    "node_type": node_type,
                    "label": element_id,
                }
            )
            existing_ids.add(element_id)
            created_node_ids.append(element_id)

    shared_node_id = None
    per_branch_ids = {}

    if mode == "shared":
        shared_node_id = label_prefix
        ensure_element(shared_node_id)
    elif mode == "per_branch":
        for branch in ["true", "false", "unknown"]:
            element_id = f"{label_prefix}_{branch}"
            per_branch_ids[branch] = element_id
            ensure_element(element_id)

    for element in elements:
        element_type = element.get("element_type") or element.get("node_type", "decision")
        if element_type != "decision":
            continue

        element_id = element.get("id")

        for branch, path_field in [
            ("true", "true_path"),
            ("false", "false_path"),
            ("unknown", "unknown_path"),
        ]:
            if element.get(path_field):
                continue

            if mode == "shared":
                target_id = shared_node_id
            elif mode == "per_branch":
                target_id = per_branch_ids[branch]
            else:
                target_id = f"{label_prefix}_{element_id}_{branch}"
                ensure_element(target_id)

            element[path_field] = target_id
            filled_count += 1

    return {
        "updated": filled_count > 0,
        "filled_count": filled_count,
        "created_node_ids": created_node_ids,
        "ruleset": _sync_ruleset_containers(ruleset, elements),
    }
