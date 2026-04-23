from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.models.models import User
from app.schemas.rulebuilder import (
    RuleBuilderWorkspace,
    RuleBuilderWorkspaceCreateRequest,
    RuleBuilderWorkspaceSummary,
    RuleBuilderWorkspaceUpdateRequest,
)


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
        self.bucket_name = bucket_name or settings.S3_BUCKET_NAME
        self.client = s3_client or boto3.client(
            "s3",
            endpoint_url=endpoint_url or settings.S3_ENDPOINT_URL,
            aws_access_key_id=access_key_id or settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=(secret_access_key or settings.S3_SECRET_ACCESS_KEY.get_secret_value()),
            region_name=region_name or settings.S3_REGION,
        )

    @staticmethod
    def _user_prefix(user: User) -> str:
        return quote(str(user.id).strip(), safe="")

    def _workspace_prefix(self, user: User) -> str:
        return f"{self.ROOT_PREFIX}/{self._user_prefix(user)}/"

    def _workspace_key(self, user: User, workspace_id: str) -> str:
        workspace_id = str(workspace_id).strip()
        if not workspace_id:
            raise ValueError("workspace_id must not be empty")
        return f"{self._workspace_prefix(user)}{quote(workspace_id, safe='')}.json"

    def rule_element_group_key(self, user: User, group_id: str) -> str:
        group_id = str(group_id).strip()
        if not group_id:
            raise ValueError("group_id must not be empty")
        return f"{self.GROUPS_PREFIX}/{self._user_prefix(user)}/{quote(group_id, safe='')}.json"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _default_name(entry_node_label: str | None, entry_node_id: str) -> str:
        if entry_node_label and entry_node_label.strip():
            return entry_node_label.strip()
        return str(entry_node_id).strip()

    def _read_workspace(self, user: User, workspace_id: str) -> RuleBuilderWorkspace:
        key = self._workspace_key(user, workspace_id)
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise RuleBuilderWorkspaceNotFoundError(f"RuleBuilder workspace not found: {workspace_id}") from exc
            raise RuleBuilderWorkspaceRepositoryError(f"RuleBuilder workspace fetch failed: {exc}") from exc
        except BotoCoreError as exc:
            raise RuleBuilderWorkspaceRepositoryError(f"RuleBuilder workspace fetch failed: {exc}") from exc

        try:
            payload = response["Body"].read().decode("utf-8")
            return RuleBuilderWorkspace.model_validate_json(payload)
        except Exception as exc:
            raise RuleBuilderWorkspaceRepositoryError(f"RuleBuilder workspace payload is invalid: {exc}") from exc

    def _write_workspace(self, workspace: RuleBuilderWorkspace, user: User) -> RuleBuilderWorkspace:
        key = self._workspace_key(user, workspace.id)
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=workspace.model_dump_json(indent=2).encode("utf-8"),
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuleBuilderWorkspaceRepositoryError(f"RuleBuilder workspace write failed: {exc}") from exc
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
        items: list[RuleBuilderWorkspaceSummary] = []
        continuation_token: str | None = None
        prefix = self._workspace_prefix(user)

        while True:
            kwargs = {
                "Bucket": self.bucket_name,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            try:
                response = self.client.list_objects_v2(**kwargs)
            except (BotoCoreError, ClientError) as exc:
                raise RuleBuilderWorkspaceRepositoryError(f"RuleBuilder workspace list failed: {exc}") from exc

            for obj in response.get("Contents", []):
                workspace_id = obj["Key"].rsplit("/", 1)[-1].removesuffix(".json")
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

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def delete_workspace(self, workspace_id: str, user: User) -> None:
        key = self._workspace_key(user, workspace_id)
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise RuleBuilderWorkspaceNotFoundError(f"RuleBuilder workspace not found: {workspace_id}") from exc
            raise RuleBuilderWorkspaceRepositoryError(f"RuleBuilder workspace delete failed: {exc}") from exc
        except BotoCoreError as exc:
            raise RuleBuilderWorkspaceRepositoryError(f"RuleBuilder workspace delete failed: {exc}") from exc

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
        key = self.rule_element_group_key(user, group_id)
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=json.dumps(payload, indent=2).encode("utf-8"),
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuleBuilderWorkspaceRepositoryError(f"RuleBuilder rule element group write failed: {exc}") from exc
