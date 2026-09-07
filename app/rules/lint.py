"""Whole-rule checks that go beyond one element: cycles, derive ordering, degenerate comparisons.

`lint_rule(rule_dict)` returns (errors, warnings). Errors block validate/compile/publish; warnings are returned.
"""

from __future__ import annotations

from typing import Any

from app.rules.expressions import ExpressionError, validate_expression
from app.rules.paths import expand_curie, expand_property_path, is_ref_value, ref_path

BRANCHES = ("true_path", "false_path", "unknown_path")


def _elements(rule: dict[str, Any]) -> list[dict[str, Any]]:
    return list(rule.get("elements") or rule.get("nodes") or [])


def _etype(el: dict[str, Any]) -> str:
    return str(el.get("element_type") or el.get("node_type") or "decision")


def _prefixes(el: dict[str, Any]) -> dict[str, str] | None:
    cfg = el.get("config")
    return cfg.get("prefixes") if isinstance(cfg, dict) else None


def _outgoing(el: dict[str, Any]) -> list[str]:
    return [str(el[b]) for b in BRANCHES if el.get(b)]


def find_cycles(rule: dict[str, Any]) -> list[list[str]]:
    by_id = {str(el["id"]): el for el in _elements(rule) if el.get("id")}
    entry = rule.get("entry") or rule.get("entry_element")
    cycles: list[list[str]] = []
    state: dict[str, int] = {}  # 0 = visiting, 1 = done
    stack: list[str] = []

    def dfs(node_id: str) -> None:
        if state.get(node_id) == 1:
            return
        if state.get(node_id) == 0:
            cycles.append(stack[stack.index(node_id) :] + [node_id])
            return
        state[node_id] = 0
        stack.append(node_id)
        el = by_id.get(node_id)
        if el is not None:
            for nxt in _outgoing(el):
                dfs(nxt)
        stack.pop()
        state[node_id] = 1

    roots = [str(entry)] if entry else []
    roots += [i for i in by_id if i not in roots]
    for root in roots:
        dfs(root)
    return cycles


def _reachable_avoiding(rule: dict[str, Any], avoid: set[str]) -> set[str]:
    """Node ids reachable from the entry without stepping on any node in `avoid`."""
    by_id = {str(el["id"]): el for el in _elements(rule) if el.get("id")}
    entry = str(rule.get("entry") or rule.get("entry_element") or "")
    seen: set[str] = set()
    stack = [entry]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in by_id or cur in avoid:
            continue
        seen.add(cur)
        stack.extend(_outgoing(by_id[cur]))
    return seen


def lint_rule(rule: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    elements = _elements(rule)
    by_id = {str(el.get("id")): el for el in elements}

    # E7: cycles
    for cycle in find_cycles(rule):
        errors.append("cycle: " + " -> ".join(cycle))

    derives = [el for el in elements if _etype(el) == "derive"]
    written: dict[str, list[str]] = {}
    for el in derives:
        prop = expand_curie(str(el.get("property_path") or ""), _prefixes(el))
        written.setdefault(prop, []).append(str(el.get("id")))
        try:
            validate_expression(str(el.get("expression") or ""))
        except ExpressionError as exc:
            errors.append(f"derive '{el.get('id')}': {exc}")
        if not el.get("true_path"):
            errors.append(f"derive '{el.get('id')}' has no true_path (the element after the value is written)")
        if not el.get("unknown_path"):
            warnings.append(f"derive '{el.get('id')}' has no unknown_path: missing inputs end the run as Unknown")

    # W2: same property written by several derive elements
    for prop, ids in written.items():
        if len(ids) > 1:
            warnings.append(
                f"property {prop} is written by several derive elements ({', '.join(ids)}); "
                "per path the last writer wins"
            )

    # W1 / W3: decisions
    for el in elements:
        if _etype(el) != "decision":
            continue
        prefixes = _prefixes(el)
        segments = expand_property_path(el.get("property_path"), prefixes)
        value = el.get("value")
        ref_segments = expand_property_path(ref_path(value), prefixes) if is_ref_value(value) else []
        if ref_segments and segments == ref_segments and el.get("operator") in ("==", "="):
            warnings.append(f"decision '{el.get('id')}' compares {el.get('property_path')} with itself (always True)")
        if derives:
            read_props = set(segments) | set(ref_segments)
            for prop in read_props & set(written):
                writers = written[prop]
                # a path from the entry that avoids every writer and still reaches this decision = read before write
                if str(el.get("id")) in _reachable_avoiding(rule, set(writers)):
                    warnings.append(
                        f"decision '{el.get('id')}' reads {prop}, which derive {writers} writes, "
                        "but not every path to it passes a writer first (possible read-before-write -> Unknown)"
                    )
    # W: derive reading a derived property that no earlier derive wrote on some path
    for el in derives:
        prefixes = _prefixes(el)
        try:
            refs = validate_expression(str(el.get("expression") or ""))
        except ExpressionError:
            continue
        for ref in refs:
            for prop in expand_property_path(ref, prefixes):
                writers = [w for w in written.get(prop, []) if w != el.get("id")]
                if not writers:
                    continue
                if str(el.get("id")) in _reachable_avoiding(rule, set(writers)):
                    warnings.append(
                        f"derive '{el.get('id')}' reads {prop}, written by {writers}, "
                        "but not every path to it passes a writer first"
                    )
    # end elements that hand over to another rule
    for el in elements:
        if _etype(el) != "end":
            continue
        cfg = el.get("config") if isinstance(el.get("config"), dict) else {}
        next_rule = cfg.get("next_rule")
        if next_rule and str(next_rule).strip() == str(rule.get("id") or "").strip():
            errors.append(f"end '{el.get('id')}' links to its own rule (next_rule = {next_rule})")
        if next_rule and str(cfg.get("end_kind") or "rule") not in ("rule", "delegate", "link"):
            warnings.append(
                f"end '{el.get('id')}' has next_rule but end_kind '{cfg.get('end_kind')}' (expected 'rule')"
            )
    # unreachable derive/decision elements
    entry = str(rule.get("entry") or rule.get("entry_element") or "")
    if entry in by_id:
        seen: set[str] = set()
        stack = [entry]
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in by_id:
                continue
            seen.add(cur)
            stack.extend(_outgoing(by_id[cur]))
        for i in by_id:
            if i not in seen:
                warnings.append(f"element '{i}' is not reachable from the entry")
    return errors, warnings
