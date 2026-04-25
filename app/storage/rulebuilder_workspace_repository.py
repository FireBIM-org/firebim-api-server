from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.models import User
from app.schemas.rulebuilder import (
    RuleBuilderWorkspace,
    RuleBuilderWorkspaceCreateRequest,
    RuleBuilderWorkspaceSummary,
    RuleBuilderWorkspaceUpdateRequest,
)
from app.storage.s3_json_store import S3JsonStoreError, S3JsonStoreNotFoundError, UserScopedS3JsonStore


class RuleBuilderWorkspaceRepositoryError(Exception):
    pass


class RuleBuilderWorkspaceNotFoundError(RuleBuilderWorkspaceRepositoryError):
    pass


class RuleBuilderWorkspaceRepository:
    ROOT_PREFIX = "rulebuilder/workspaces"
    GROUPS_PREFIX = "rulebuilder/rule-element-groups"

    def __init__(
        self,
        *,
        bucket_name: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region_name: str | None = None,
        s3_client=None,
    ) -> None:
        self.store = UserScopedS3JsonStore(
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region_name=region_name,
            s3_client=s3_client,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _default_name(entry_node_label: str | None, entry_node_id: str) -> str:
        if entry_node_label and entry_node_label.strip():
            return entry_node_label.strip()
        return str(entry_node_id).strip()

    def _read_workspace(self, user: User, workspace_id: str) -> RuleBuilderWorkspace:
        try:
            return self.store.read_model(
                self.ROOT_PREFIX,
                user,
                workspace_id,
                model_type=RuleBuilderWorkspace,
                not_found_message=f"RuleBuilder workspace not found: {workspace_id}",
                fetch_error_message="RuleBuilder workspace fetch failed",
                payload_error_message="RuleBuilder workspace payload is invalid",
            )
        except S3JsonStoreNotFoundError as exc:
            raise RuleBuilderWorkspaceNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise RuleBuilderWorkspaceRepositoryError(str(exc)) from exc

    def _write_workspace(self, workspace: RuleBuilderWorkspace, user: User) -> RuleBuilderWorkspace:
        try:
            self.store.write_model(
                self.ROOT_PREFIX,
                user,
                workspace.id,
                workspace,
                write_error_message="RuleBuilder workspace write failed",
            )
        except S3JsonStoreError as exc:
            raise RuleBuilderWorkspaceRepositoryError(str(exc)) from exc
        return workspace

    def create_workspace(self, payload: RuleBuilderWorkspaceCreateRequest, user: User) -> RuleBuilderWorkspace:
        now = self._now()
        workspace = RuleBuilderWorkspace(
            id=str(uuid.uuid4()),
            name=self._default_name(payload.name or payload.entry_node_label, payload.entry_node_id),
            entry_node_id=payload.entry_node_id,
            entry_node_label=payload.entry_node_label,
            viewport=payload.viewport,
            nodes=payload.nodes,
            edges=payload.edges,
            created_at=now,
            updated_at=now,
        )
        return self._write_workspace(workspace, user)

    def update_workspace(
        self, workspace_id: str, payload: RuleBuilderWorkspaceUpdateRequest, user: User
    ) -> RuleBuilderWorkspace:
        existing = self._read_workspace(user, workspace_id)
        workspace = RuleBuilderWorkspace(
            id=existing.id,
            name=self._default_name(payload.name or payload.entry_node_label, payload.entry_node_id),
            entry_node_id=payload.entry_node_id,
            entry_node_label=payload.entry_node_label,
            viewport=payload.viewport,
            nodes=payload.nodes,
            edges=payload.edges,
            created_at=existing.created_at,
            updated_at=self._now(),
        )
        return self._write_workspace(workspace, user)

    def get_workspace(self, workspace_id: str, user: User) -> RuleBuilderWorkspace:
        return self._read_workspace(user, workspace_id)

    def list_workspaces(self, user: User) -> list[RuleBuilderWorkspaceSummary]:
        try:
            workspace_ids = self.store.list_document_ids(self.ROOT_PREFIX, user)
        except S3JsonStoreError as exc:
            raise RuleBuilderWorkspaceRepositoryError(f"RuleBuilder workspace list failed: {exc}") from exc

        items: list[RuleBuilderWorkspaceSummary] = []
        for workspace_id in workspace_ids:
            workspace = self._read_workspace(user, workspace_id)
            items.append(
                RuleBuilderWorkspaceSummary(
                    id=workspace.id,
                    name=workspace.name,
                    entry_node_id=workspace.entry_node_id,
                    entry_node_label=workspace.entry_node_label,
                    created_at=workspace.created_at,
                    updated_at=workspace.updated_at,
                    node_count=len(workspace.nodes),
                    edge_count=len(workspace.edges),
                )
            )

        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def delete_workspace(self, workspace_id: str, user: User) -> None:
        try:
            self.store.delete_document(
                self.ROOT_PREFIX,
                user,
                workspace_id,
                not_found_message=f"RuleBuilder workspace not found: {workspace_id}",
                delete_error_message="RuleBuilder workspace delete failed",
            )
        except S3JsonStoreNotFoundError as exc:
            raise RuleBuilderWorkspaceNotFoundError(str(exc)) from exc
        except S3JsonStoreError as exc:
            raise RuleBuilderWorkspaceRepositoryError(str(exc)) from exc

    def delete_workspaces(self, workspace_ids: list[str], user: User) -> tuple[list[str], list[str]]:
        deleted_ids: list[str] = []
        not_found_ids: list[str] = []

        for workspace_id in workspace_ids:
            try:
                self.delete_workspace(workspace_id, user)
                deleted_ids.append(workspace_id)
            except RuleBuilderWorkspaceNotFoundError:
                not_found_ids.append(workspace_id)

        return deleted_ids, not_found_ids

    def write_rule_element_group_manifest(self, user: User, payload: dict) -> None:
        group_id = payload["group_id"]
        try:
            self.store.write_dict(
                self.GROUPS_PREFIX,
                user,
                group_id,
                payload,
                write_error_message="RuleBuilder rule element group write failed",
            )
        except S3JsonStoreError as exc:
            raise RuleBuilderWorkspaceRepositoryError(str(exc)) from exc
