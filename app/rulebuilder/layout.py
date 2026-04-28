from __future__ import annotations

from collections.abc import Iterable

from app.schemas.rulebuilder import (
    RuleBuilderCanvasEdge,
    RuleBuilderCanvasNode,
    RuleBuilderCanvasPosition,
    RuleBuilderViewport,
    RuleBuilderWorkspaceCreateRequest,
)
from app.schemas.rules import RuleDraft, RuleElementPayload

BRANCH_ORDER = ("true", "false", "unknown")
BRANCH_X_OFFSETS = {
    "true": -340.0,
    "false": 0.0,
    "unknown": 340.0,
}
ROOT_X = 800.0
ROOT_Y = -350.0
ROW_STEP_Y = 290.0
END_ROW_GAP_Y = 440.0
END_MIN_SPACING_X = 310.0
COLLISION_OFFSET_X = 28.0
COLLISION_OFFSET_Y = 36.0
DEFAULT_VIEWPORT = RuleBuilderViewport(x=-25.0, y=234.0, zoom=0.5)


def build_workspace_from_rule(
    rule: RuleDraft,
    *,
    workspace_name: str | None = None,
    viewport: RuleBuilderViewport | None = None,
) -> RuleBuilderWorkspaceCreateRequest:
    elements_by_id = {element.id: element for element in rule.elements}
    if rule.entry not in elements_by_id:
        raise ValueError(f"Rule entry node '{rule.entry}' was not found among rule elements")

    reachable_ids = _collect_reachable_node_ids(rule.entry, elements_by_id)
    ordered_elements = [elements_by_id[node_id] for node_id in reachable_ids]
    remaining_elements = [element for element in rule.elements if element.id not in reachable_ids]

    positions: dict[str, tuple[float, float]] = {}
    occupied_positions: dict[tuple[float, float], str] = {}
    end_desired_x: dict[str, list[float]] = {}

    _place_decision_subtree(
        rule.entry,
        x=ROOT_X,
        y=ROOT_Y,
        elements_by_id=elements_by_id,
        positions=positions,
        occupied_positions=occupied_positions,
        end_desired_x=end_desired_x,
    )

    _place_end_nodes(
        ordered_elements,
        positions=positions,
        occupied_positions=occupied_positions,
        end_desired_x=end_desired_x,
    )
    _place_unreachable_nodes(
        remaining_elements,
        positions=positions,
        occupied_positions=occupied_positions,
    )

    nodes = [
        RuleBuilderCanvasNode(
            canvas_node_id=element.id,
            position=RuleBuilderCanvasPosition(x=positions[element.id][0], y=positions[element.id][1]),
            rule=element,
        )
        for element in [*ordered_elements, *remaining_elements]
    ]
    edges = _build_edges([*ordered_elements, *remaining_elements], elements_by_id)

    entry_element = elements_by_id[rule.entry]
    return RuleBuilderWorkspaceCreateRequest(
        name=workspace_name or entry_element.label or rule.id,
        entry_node_id=rule.entry,
        entry_node_label=entry_element.label,
        viewport=viewport or DEFAULT_VIEWPORT,
        nodes=nodes,
        edges=edges,
    )


def _collect_reachable_node_ids(entry_id: str, elements_by_id: dict[str, RuleElementPayload]) -> list[str]:
    order: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited or node_id not in elements_by_id:
            return
        visited.add(node_id)
        order.append(node_id)
        node = elements_by_id[node_id]
        for branch in BRANCH_ORDER:
            target_id = getattr(node, f"{branch}_path")
            if target_id:
                visit(target_id)

    visit(entry_id)
    return order


def _place_decision_subtree(
    node_id: str,
    *,
    x: float,
    y: float,
    elements_by_id: dict[str, RuleElementPayload],
    positions: dict[str, tuple[float, float]],
    occupied_positions: dict[tuple[float, float], str],
    end_desired_x: dict[str, list[float]],
) -> None:
    if node_id in positions or node_id not in elements_by_id:
        return

    x, y = _reserve_position(node_id, x, y, positions, occupied_positions)
    positions[node_id] = (x, y)
    occupied_positions[(x, y)] = node_id

    node = elements_by_id[node_id]
    if node.element_type == "end":
        return

    for branch in BRANCH_ORDER:
        target_id = getattr(node, f"{branch}_path")
        if not target_id or target_id not in elements_by_id:
            continue
        target = elements_by_id[target_id]
        desired_x = x + BRANCH_X_OFFSETS[branch]
        if target.element_type == "end":
            end_desired_x.setdefault(target_id, []).append(desired_x)
            continue
        _place_decision_subtree(
            target_id,
            x=desired_x,
            y=y + ROW_STEP_Y,
            elements_by_id=elements_by_id,
            positions=positions,
            occupied_positions=occupied_positions,
            end_desired_x=end_desired_x,
        )


def _place_end_nodes(
    ordered_elements: Iterable[RuleElementPayload],
    *,
    positions: dict[str, tuple[float, float]],
    occupied_positions: dict[tuple[float, float], str],
    end_desired_x: dict[str, list[float]],
) -> None:
    end_ids = [element.id for element in ordered_elements if element.element_type == "end"]
    if not end_ids:
        return

    decision_y_values = [y for node_id, (_, y) in positions.items() if node_id not in end_ids]
    bottom_y = (max(decision_y_values) if decision_y_values else ROOT_Y) + END_ROW_GAP_Y

    ranked_end_ids = sorted(
        end_ids,
        key=lambda node_id: _average(end_desired_x.get(node_id) or [ROOT_X]),
    )

    current_x: float | None = None
    for node_id in ranked_end_ids:
        desired_x = _average(end_desired_x.get(node_id) or [ROOT_X])
        if current_x is None:
            final_x = desired_x
        else:
            final_x = max(desired_x, current_x + END_MIN_SPACING_X)
        final_x, final_y = _reserve_position(node_id, final_x, bottom_y, positions, occupied_positions)
        positions[node_id] = (final_x, final_y)
        occupied_positions[(final_x, final_y)] = node_id
        current_x = final_x


def _place_unreachable_nodes(
    remaining_elements: list[RuleElementPayload],
    *,
    positions: dict[str, tuple[float, float]],
    occupied_positions: dict[tuple[float, float], str],
) -> None:
    if not remaining_elements:
        return

    max_y = max((y for _, y in positions.values()), default=ROOT_Y)
    x = ROOT_X + END_MIN_SPACING_X
    y = max_y + ROW_STEP_Y

    for element in remaining_elements:
        final_x, final_y = _reserve_position(element.id, x, y, positions, occupied_positions)
        positions[element.id] = (final_x, final_y)
        occupied_positions[(final_x, final_y)] = element.id
        y = final_y + ROW_STEP_Y


def _build_edges(
    elements: Iterable[RuleElementPayload],
    elements_by_id: dict[str, RuleElementPayload],
) -> list[RuleBuilderCanvasEdge]:
    edges: list[RuleBuilderCanvasEdge] = []
    for element in elements:
        for branch in BRANCH_ORDER:
            target_id = getattr(element, f"{branch}_path")
            if not target_id or target_id not in elements_by_id:
                continue
            edges.append(
                RuleBuilderCanvasEdge(
                    id=f"{element.id}::{branch}::{target_id}",
                    source=element.id,
                    source_handle=branch,
                    target=target_id,
                    target_handle="in",
                )
            )
    return edges


def _reserve_position(
    node_id: str,
    x: float,
    y: float,
    positions: dict[str, tuple[float, float]],
    occupied_positions: dict[tuple[float, float], str],
) -> tuple[float, float]:
    x = float(round(x))
    y = float(round(y))
    while True:
        owner = occupied_positions.get((x, y))
        if owner is None or owner == node_id:
            return x, y
        x += COLLISION_OFFSET_X
        y += COLLISION_OFFSET_Y


def _average(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values)
