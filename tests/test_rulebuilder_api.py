from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_DB", "firebim")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_BUCKET_NAME", "firebim")
os.environ.setdefault("S3_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("S3_SECRET_ACCESS_KEY", "minioadmin")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

from app.api import rulebuilder as rulebuilder_api
from app.rulebuilder.layout import build_workspace_from_rule
from app.schemas.rulebuilder import RuleBuilderWorkspace, RuleBuilderWorkspaceSummary
from app.schemas.rules import RuleDraft, RuleElementPayload


class DummyRepo:
    ROOT_PREFIX = "rulebuilder/workspaces"

    def __init__(self):
        self.workspace = RuleBuilderWorkspace(
            id="ws_123",
            name="Check Rooms",
            entry_node_id="check_room_area",
            entry_node_label="Check Rooms",
            viewport={"x": 10, "y": 20, "zoom": 1.0},
            nodes=[
                {
                    "canvas_node_id": "canvas_a",
                    "position": {"x": 100, "y": 240},
                    "rule": {
                        "id": "check_room_area",
                        "element_type": "decision",
                        "target_class": "fbo:Room",
                        "property_path": "fbo:roomArea",
                        "operator": ">=",
                        "value": 7,
                        "true_path": "ok_node",
                        "false_path": "fail_node",
                        "unknown_path": "unknown_node",
                        "label": "Check Rooms",
                        "config": {},
                    },
                }
            ],
            edges=[
                {
                    "id": "edge_1",
                    "source": "canvas_a",
                    "source_handle": "true",
                    "target": "canvas_b",
                    "target_handle": "input",
                }
            ],
            created_at=datetime(2026, 4, 23, 8, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 23, 9, 0, tzinfo=UTC),
        )
        self.group_manifest = None

    def create_workspace(self, payload, user):
        _ = user
        self.last_create_payload = payload
        return RuleBuilderWorkspace(
            **payload.model_dump(),
            id="ws_new",
            created_at=datetime(2026, 4, 23, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 23, 10, 0, tzinfo=UTC),
        )

    def update_workspace(self, workspace_id, payload, user):
        _ = payload, user
        return self.workspace.model_copy(update={"id": workspace_id})

    def list_workspaces(self, user):
        _ = user
        return [
            RuleBuilderWorkspaceSummary(
                id=self.workspace.id,
                name=self.workspace.name,
                entry_node_id=self.workspace.entry_node_id,
                entry_node_label=self.workspace.entry_node_label,
                created_at=self.workspace.created_at,
                updated_at=self.workspace.updated_at,
                node_count=len(self.workspace.nodes),
                edge_count=len(self.workspace.edges),
            )
        ]

    def get_workspace(self, workspace_id, user):
        _ = user
        return self.workspace.model_copy(update={"id": workspace_id})

    def delete_workspace(self, workspace_id, user):
        _ = workspace_id, user
        return None

    def delete_workspaces(self, workspace_ids, user):
        _ = user
        return workspace_ids[:1], workspace_ids[1:]

    def write_rule_element_group_manifest(self, user, payload):
        _ = user
        self.group_manifest = payload


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(rulebuilder_api.router)
    repo = DummyRepo()
    monkeypatch.setattr(rulebuilder_api, "repo", repo)
    app.dependency_overrides[rulebuilder_api.get_current_active_user] = lambda: object()
    monkeypatch.setattr(
        rulebuilder_api,
        "_publish_rule_element_snapshot",
        lambda node_rule: (node_rule.id, f"https://firebim.org/graphs/rule-elements/{node_rule.id}"),
    )
    return TestClient(app)


@pytest.fixture()
def workspace_payload():
    return {
        "name": "Check Rooms",
        "entry_node_id": "check_room_area",
        "entry_node_label": "Check Rooms",
        "viewport": {"x": 10, "y": 20, "zoom": 1.0},
        "nodes": [
            {
                "canvas_node_id": "canvas_a",
                "position": {"x": 100, "y": 240},
                "rule": {
                    "id": "check_room_area",
                    "element_type": "decision",
                    "target_class": "fbo:Room",
                    "property_path": "fbo:roomArea",
                    "operator": ">=",
                    "value": 7,
                    "true_path": "ok_node",
                    "false_path": "fail_node",
                    "unknown_path": "unknown_node",
                    "label": "Check Rooms",
                    "config": {},
                },
            }
        ],
        "edges": [
            {
                "id": "e-start-123-true-decision-1",
                "source": "canvas_a",
                "source_handle": "true",
                "target": "canvas_b",
                "target_handle": "input",
            }
        ],
    }


def test_create_workspace(client: TestClient, workspace_payload):
    response = client.post("/rulebuilder/workspaces", json=workspace_payload)
    assert response.status_code == 201
    body = response.json()
    assert body["workspace"]["id"] == "ws_new"
    assert body["workspace"]["name"] == "Check Rooms"


def test_update_workspace(client: TestClient, workspace_payload):
    response = client.put("/rulebuilder/workspaces/ws_123", json=workspace_payload)
    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["id"] == "ws_123"
    assert body["workspace"]["viewport"]["x"] == 10


def test_list_workspaces(client: TestClient):
    response = client.get("/rulebuilder/workspaces")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["workspaces"][0]["node_count"] == 1


def test_get_workspace(client: TestClient):
    response = client.get("/rulebuilder/workspaces/ws_123")
    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["entry_node_id"] == "check_room_area"
    assert body["workspace"]["nodes"][0]["position"]["x"] == 100


def test_delete_workspace(client: TestClient):
    response = client.delete("/rulebuilder/workspaces/ws_123")
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "workspace_id": "ws_123"}


def test_delete_workspaces_batch(client: TestClient):
    response = client.post(
        "/rulebuilder/workspaces/delete-batch",
        json={"workspace_ids": ["ws_123", "missing"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deleted_ids"] == ["ws_123"]
    assert body["not_found_ids"] == ["missing"]


def test_export_rule_elements(client: TestClient):
    response = client.post("/rulebuilder/workspaces/ws_123/export-rule-elements")
    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is True
    assert body["workspace_id"] == "ws_123"
    assert body["group_name"] == "Check Rooms"
    assert body["created_count"] == 1
    assert body["elements"] == [
        {
            "source_id": "check_room_area",
            "element_id": "check_room_area",
            "graph_uri": "https://firebim.org/graphs/rule-elements/check_room_area",
        }
    ]


def test_create_workspace_from_rule(client: TestClient, monkeypatch):
    rule = RuleDraft(
        id="rule_rooms",
        entry="start_1",
        elements=[
            RuleElementPayload(
                id="start_1",
                element_type="start",
                true_path="decision_root",
                label="Start",
            ),
            RuleElementPayload(
                id="decision_root",
                element_type="decision",
                true_path="decision_true",
                false_path="decision_false",
                unknown_path="decision_unknown",
                label="Root",
            ),
            RuleElementPayload(
                id="decision_true",
                element_type="decision",
                true_path="end_pass",
                false_path="end_fail",
                unknown_path="end_missing",
                label="True branch",
            ),
            RuleElementPayload(
                id="decision_false",
                element_type="decision",
                true_path="end_pass",
                false_path="end_fail",
                unknown_path="end_missing",
                label="False branch",
            ),
            RuleElementPayload(
                id="decision_unknown",
                element_type="decision",
                true_path="end_pass",
                false_path="end_fail",
                unknown_path="end_missing",
                label="Unknown branch",
            ),
            RuleElementPayload(id="end_pass", element_type="end", label="Pass"),
            RuleElementPayload(id="end_fail", element_type="end", label="Fail"),
            RuleElementPayload(id="end_missing", element_type="end", label="Missing"),
        ],
    )
    monkeypatch.setattr(rulebuilder_api, "_load_rule_draft", lambda rule_id: rule)

    response = client.post(
        "/rulebuilder/rules/rule_rooms/workspace",
        json={"name": "Layout from rule"},
    )

    assert response.status_code == 201
    body = response.json()
    workspace = body["workspace"]
    assert workspace["name"] == "Layout from rule"
    assert workspace["entry_node_id"] == "start_1"
    assert len(workspace["nodes"]) == 8
    positions = {node["canvas_node_id"]: node["position"] for node in workspace["nodes"]}
    assert positions["start_1"]["x"] == 800
    assert positions["start_1"]["y"] == -350
    assert positions["decision_true"]["x"] < positions["decision_false"]["x"] < positions["decision_unknown"]["x"]
    assert positions["end_pass"]["y"] == positions["end_fail"]["y"] == positions["end_missing"]["y"]
    assert positions["end_pass"]["x"] < positions["end_fail"]["x"] < positions["end_missing"]["x"]


def test_build_workspace_from_rule_offsets_collisions():
    rule = RuleDraft(
        id="rule_collision",
        entry="start_a",
        elements=[
            RuleElementPayload(id="start_a", element_type="start", true_path="decision_a", label="Start"),
            RuleElementPayload(
                id="decision_a",
                element_type="decision",
                true_path="shared_decision",
                false_path="shared_decision",
                unknown_path="shared_decision",
                label="A",
            ),
            RuleElementPayload(
                id="shared_decision",
                element_type="decision",
                true_path="end_a",
                false_path="end_b",
                unknown_path="end_c",
                label="Shared",
            ),
            RuleElementPayload(id="end_a", element_type="end", label="A end"),
            RuleElementPayload(id="end_b", element_type="end", label="B end"),
            RuleElementPayload(id="end_c", element_type="end", label="C end"),
        ],
    )

    workspace = build_workspace_from_rule(rule, workspace_name="Collision test")
    positions = {node.canvas_node_id: (node.position.x, node.position.y) for node in workspace.nodes}

    assert positions["start_a"] == (800.0, -350.0)
    assert positions["decision_a"][1] > positions["start_a"][1]
    assert positions["shared_decision"][1] > positions["decision_a"][1]
    assert len(set(positions.values())) == len(positions)
