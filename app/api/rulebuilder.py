from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.jwt import get_current_active_user
from app.models.models import User
from app.rulebuilder.layout import build_workspace_from_rule
from app.rules.decompiler import decompile_ruleset_ttl
from app.rules.element_validator import validate_rule_element_payload
from app.rules.models import fbRuleElement
from app.rules.publisher import (
    default_rule_element_graph_uri,
    default_rule_graph_uri,
    fetch_graph_ttl,
    publish_graph_ttl,
    remember_rule_element_graph,
)
from app.rules.shacl_builder import SHACLBuilder
from app.schemas.rulebuilder import (
    RuleBuilderExportRuleElementsResponse,
    RuleBuilderWorkspaceBatchDeleteRequest,
    RuleBuilderWorkspaceBatchDeleteResponse,
    RuleBuilderWorkspaceCreateRequest,
    RuleBuilderWorkspaceDeleteResponse,
    RuleBuilderWorkspaceFromRuleRequest,
    RuleBuilderWorkspaceListResponse,
    RuleBuilderWorkspaceResponse,
    RuleBuilderWorkspaceUpdateRequest,
)
from app.schemas.rules import RuleDraft, RuleElementPayload
from app.storage.graph_store import GraphStoreNotFoundError
from app.storage.rulebuilder_workspace_repository import (
    RuleBuilderWorkspaceNotFoundError,
    RuleBuilderWorkspaceRepository,
    RuleBuilderWorkspaceRepositoryError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rulebuilder", tags=["rulebuilder"])

repo = RuleBuilderWorkspaceRepository()


def _publish_rule_element_snapshot(node_rule) -> tuple[str, str]:
    graph_uri = default_rule_element_graph_uri(node_rule.id)
    node_data = node_rule.as_internal_dict()
    validate_rule_element_payload(node_data)

    element = fbRuleElement(
        id=node_rule.id,
        target_class=node_rule.target_class,
        property_path=node_rule.property_path,
        operator=node_rule.operator,
        value=node_rule.value,
        true_path=node_rule.true_path,
        false_path=node_rule.false_path,
        unknown_path=node_rule.unknown_path,
        label=node_rule.label,
        node_type=node_rule.element_type,
        config=node_rule.config,
    )

    builder = SHACLBuilder()
    builder.add_rule_element(element)
    ttl = builder.get_graph().serialize(format="turtle")
    publish_graph_ttl(ttl_text=str(ttl), dataset="rulebook", graph_uri=graph_uri)
    remember_rule_element_graph(
        graph_uri=graph_uri,
        element_id=element.id,
        element_type=node_rule.element_type,
        label=node_rule.label,
    )
    return element.id, graph_uri


def _normalize_rule_draft(raw_rule: dict | None) -> RuleDraft:
    if not raw_rule:
        raise ValueError("Rule could not be decompiled")

    elements = raw_rule.get("elements") or raw_rule.get("nodes") or []
    return RuleDraft(
        id=raw_rule["id"],
        entry=raw_rule["entry"],
        elements=[
            RuleElementPayload(
                id=element["id"],
                element_type=element.get("element_type") or element.get("node_type") or "decision",
                target_class=element.get("target_class"),
                property_path=element.get("property_path"),
                operator=element.get("operator"),
                value=element.get("value"),
                true_path=element.get("true_path"),
                false_path=element.get("false_path"),
                unknown_path=element.get("unknown_path"),
                label=element.get("label"),
                config=element.get("config") or {},
            )
            for element in elements
        ],
    )


def _load_rule_draft(rule_id: str) -> RuleDraft:
    graph_uri = default_rule_graph_uri(rule_id)
    ttl = fetch_graph_ttl(dataset="rulebook", graph_uri=graph_uri)
    return _normalize_rule_draft(decompile_ruleset_ttl(ttl))


@router.post("/workspaces", response_model=RuleBuilderWorkspaceResponse, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: RuleBuilderWorkspaceCreateRequest,
    current_user: User = Depends(get_current_active_user),
) -> RuleBuilderWorkspaceResponse:
    try:
        workspace = repo.create_workspace(payload, current_user)
        return RuleBuilderWorkspaceResponse(workspace=workspace)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuleBuilderWorkspaceRepositoryError as exc:
        logger.exception("Failed to create RuleBuilder workspace", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/rules/{rule_id}/workspace",
    response_model=RuleBuilderWorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace_from_rule(
    rule_id: str,
    payload: RuleBuilderWorkspaceFromRuleRequest,
    current_user: User = Depends(get_current_active_user),
) -> RuleBuilderWorkspaceResponse:
    try:
        rule = _load_rule_draft(rule_id)
        workspace_payload = build_workspace_from_rule(
            rule,
            workspace_name=payload.name,
            viewport=payload.viewport,
        )
        workspace = repo.create_workspace(workspace_payload, current_user)
        return RuleBuilderWorkspaceResponse(workspace=workspace)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuleBuilderWorkspaceRepositoryError as exc:
        logger.exception("Failed to create RuleBuilder workspace from rule", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.put("/workspaces/{workspace_id}", response_model=RuleBuilderWorkspaceResponse)
def update_workspace(
    workspace_id: str,
    payload: RuleBuilderWorkspaceUpdateRequest,
    current_user: User = Depends(get_current_active_user),
) -> RuleBuilderWorkspaceResponse:
    try:
        workspace = repo.update_workspace(workspace_id, payload, current_user)
        return RuleBuilderWorkspaceResponse(workspace=workspace)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuleBuilderWorkspaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuleBuilderWorkspaceRepositoryError as exc:
        logger.exception("Failed to update RuleBuilder workspace", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/workspaces", response_model=RuleBuilderWorkspaceListResponse)
def list_workspaces(current_user: User = Depends(get_current_active_user)) -> RuleBuilderWorkspaceListResponse:
    try:
        workspaces = repo.list_workspaces(current_user)
        return RuleBuilderWorkspaceListResponse(count=len(workspaces), workspaces=workspaces)
    except RuleBuilderWorkspaceRepositoryError as exc:
        logger.exception("Failed to list RuleBuilder workspaces", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}", response_model=RuleBuilderWorkspaceResponse)
def get_workspace(
    workspace_id: str, current_user: User = Depends(get_current_active_user)
) -> RuleBuilderWorkspaceResponse:
    try:
        workspace = repo.get_workspace(workspace_id, current_user)
        return RuleBuilderWorkspaceResponse(workspace=workspace)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuleBuilderWorkspaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuleBuilderWorkspaceRepositoryError as exc:
        logger.exception("Failed to fetch RuleBuilder workspace", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.delete("/workspaces/{workspace_id}", response_model=RuleBuilderWorkspaceDeleteResponse)
def delete_workspace(
    workspace_id: str, current_user: User = Depends(get_current_active_user)
) -> RuleBuilderWorkspaceDeleteResponse:
    try:
        repo.delete_workspace(workspace_id, current_user)
        return RuleBuilderWorkspaceDeleteResponse(deleted=True, workspace_id=workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuleBuilderWorkspaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuleBuilderWorkspaceRepositoryError as exc:
        logger.exception("Failed to delete RuleBuilder workspace", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/workspaces/delete-batch", response_model=RuleBuilderWorkspaceBatchDeleteResponse)
def delete_workspaces(
    payload: RuleBuilderWorkspaceBatchDeleteRequest,
    current_user: User = Depends(get_current_active_user),
) -> RuleBuilderWorkspaceBatchDeleteResponse:
    try:
        deleted_ids, not_found_ids = repo.delete_workspaces(payload.workspace_ids, current_user)
        return RuleBuilderWorkspaceBatchDeleteResponse(
            deleted_ids=deleted_ids,
            not_found_ids=not_found_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuleBuilderWorkspaceRepositoryError as exc:
        logger.exception("Failed to batch delete RuleBuilder workspaces", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/export-rule-elements", response_model=RuleBuilderExportRuleElementsResponse)
def export_workspace_rule_elements(
    workspace_id: str,
    current_user: User = Depends(get_current_active_user),
) -> RuleBuilderExportRuleElementsResponse:
    try:
        workspace = repo.get_workspace(workspace_id, current_user)
        seen_ids: set[str] = set()
        saved_elements: list[dict[str, str]] = []
        duplicate_ids: list[str] = []

        for node in workspace.nodes:
            rule_id = node.rule.id
            if rule_id in seen_ids:
                duplicate_ids.append(rule_id)
                continue
            seen_ids.add(rule_id)
            element_id, graph_uri = _publish_rule_element_snapshot(node.rule)
            saved_elements.append(
                {
                    "source_id": rule_id,
                    "element_id": element_id,
                    "graph_uri": graph_uri,
                }
            )

        group_id = str(uuid.uuid4())
        group_name = workspace.entry_node_label or workspace.name
        repo.write_rule_element_group_manifest(
            current_user,
            {
                "group_id": group_id,
                "group_name": group_name,
                "workspace_id": workspace.id,
                "entry_node_id": workspace.entry_node_id,
                "entry_node_label": workspace.entry_node_label,
                "elements": saved_elements,
                "duplicate_rule_element_ids": duplicate_ids,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

        return RuleBuilderExportRuleElementsResponse(
            saved=True,
            workspace_id=workspace.id,
            group_id=group_id,
            group_name=group_name,
            entry_node_id=workspace.entry_node_id,
            entry_node_label=workspace.entry_node_label,
            created_count=len(saved_elements),
            elements=saved_elements,
            duplicate_rule_element_ids=duplicate_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuleBuilderWorkspaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuleBuilderWorkspaceRepositoryError as exc:
        logger.exception("Failed to export RuleBuilder workspace rule elements", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
