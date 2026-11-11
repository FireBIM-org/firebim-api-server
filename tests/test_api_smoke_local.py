import os
from types import SimpleNamespace

# Minimal settings needed before importing the API modules.
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

import pytest
from fastapi import FastAPI

from app.api import checks as checks_api
from app.api import graphs as graphs_api
from app.api import rules as rules_api


class DummyRepo:
    def health(self):
        return True

    def list_graphs(self):
        return [
            {
                "graph_uri": "https://firebim.org/graphs/buildings/test-graph",
                "triple_count": 12,
            }
        ]

    def publish_graph(self, graph_id: str, ttl: str):
        assert graph_id == "test-graph"
        assert "@prefix" in ttl or ttl.strip()
        return (f"https://firebim.org/graphs/buildings/{graph_id}", False)

    def delete_graph(self, graph_id: str):
        return f"https://firebim.org/graphs/buildings/{graph_id}"

    def fetch_graph(self, graph_id: str):
        return (
            f"https://firebim.org/graphs/buildings/{graph_id}",
            '@prefix ex: <http://example.org/> .\nex:A ex:has "x" .',
        )


class DummySerializedGraph:
    def serialize(self, format="turtle"):
        return '@prefix ex: <http://example.org/> .\nex:Rule ex:has "ttl" .'


class DummyBuilder:
    def __init__(self):
        self._graph = DummySerializedGraph()

    def add_rule_set(self, rule):
        self.rule = rule

    def add_rule_element(self, element):
        self.element = element

    def get_graph(self):
        return self._graph


class DummyRule:
    def __init__(self, rule_id="room_area_rule", entry="check_room_area", elements=None):
        self.id = rule_id
        self.entry = entry
        self.elements = elements or [
            SimpleNamespace(id="check_room_area"),
            SimpleNamespace(id="room_ok"),
            SimpleNamespace(id="room_fail"),
            SimpleNamespace(id="room_unknown"),
        ]


class DummyResult:
    def __init__(self):
        self.summary = SimpleNamespace(
            total_targets=1,
            passed=1,
            failed=0,
            unknown=0,
            conforms=True,
            terminal_counts={"room_ok": 1},
            missing_data_categories={},
        )
        self.engine = {
            "mode": "standard_compiled_runtime",
            "runtime": "CheckRuntime",
            "evaluator": "PyShaclAtomicEvaluator",
            "target_resolver": "TargetResolver",
            "planner_strategy": "vertical",
            "diagnostics_enabled": False,
        }
        self.targets = [
            SimpleNamespace(
                target_node="https://firebim.org/instances/room-1",
                target_class="http://example.org/fbo/1.0/Room",
                final_status="True",
                message="Room passed",
                terminal_node_id="room_ok",
                trace=[
                    SimpleNamespace(
                        node_id="check_room_area",
                        node_type="decision",
                        status="True",
                        next_node_id="room_ok",
                        evidence={"property": "http://example.org/fbo/1.0/roomArea"},
                    )
                ],
                evidence={"value": 25},
            )
        ]
        self.report_text = "Validation report"
        self.report_graph_ttl = "@prefix sh: <http://www.w3.org/ns/shacl#> ."
        self.compiled_rule = {
            "entry_node_id": "check_room_area",
            "start_node_id": "check_room_area",
            "end_nodes": [
                {
                    "node_id": "room_ok",
                    "label": "passed",
                    "end_kind": "passed",
                    "output_text": "passed",
                    "terminal_value": "passed",
                    "analysis_code": "123",
                }
            ],
        }
        self.debug = {
            "runner": "compiled",
            "timings_ms": {"run_rule": 1.5},
            "counters": {"evaluated_nodes": 1},
            "node_timings_ms": {"check_room_area": 1.0},
            "class_timings_ms": {"http://example.org/fbo/1.0/Room": 1.0},
            "transition_timings_ms": {"check_room_area->room_ok:forward": 0.5},
            "completed_chains": [
                {
                    "target_node": "https://firebim.org/instances/room-1",
                    "terminal_node_id": "room_ok",
                    "final_status": "True",
                    "terminal_value": "passed",
                    "duration_ms": 1.0,
                    "node_sequence": ["check_room_area", "room_ok"],
                }
            ],
            "transition_inspections": [],
            "node_inspections": [],
            "terminal_failures": [],
            "sequence_events": [],
            "planner": {"strategy": "vertical"},
        }


class DummyCheckEngine:
    def run_rule(self, **kwargs):
        assert kwargs["graph_id"] == "building-a"
        assert kwargs["rule_id"] == "room_area_rule"
        assert kwargs["graph_uri"].endswith("/building-a")
        return DummyResult()


class BlindRunnerResult(dict):
    @property
    def debug(self):
        return self.get("debug", {})


class DummyBlindRunner:
    def run_rule(self, **kwargs):
        assert kwargs["graph_id"] == "building-a"
        assert kwargs["rule_id"] == "room_area_rule"
        return BlindRunnerResult(
            success=True,
            mode="blind_shacl_construct",
            summary={
                "total_targets": 1,
                "passed": 0,
                "failed": 1,
                "unknown": 0,
                "conforms": False,
                "terminal_counts": {"room_fail": 1},
                "missing_data_categories": {},
            },
            end_outputs=[
                {
                    "target_node": "https://firebim.org/instances/room-1",
                    "terminal_node_id": "room_fail",
                    "terminal_value": "False",
                    "final_status": "False",
                }
            ],
            terminal_counts={"room_fail": 1},
            message_counts={"Room too small": 1},
            debug={"runner": "blind"},
        )


@pytest.fixture()
def client(monkeypatch, authenticated_client_factory):
    app = FastAPI()
    app.include_router(graphs_api.router)
    app.include_router(rules_api.router)
    app.include_router(checks_api.router)

    # Graphs API
    monkeypatch.setattr(graphs_api, "repo", DummyRepo())

    # Rules API
    monkeypatch.setattr(rules_api, "_build_rule", lambda payload: DummyRule(rule_id=payload.id, entry=payload.entry))
    monkeypatch.setattr(rules_api, "SHACLBuilder", DummyBuilder)
    monkeypatch.setattr(rules_api, "publish_graph_ttl", lambda **kwargs: None)
    monkeypatch.setattr(rules_api, "delete_graph_ttl", lambda **kwargs: None)
    monkeypatch.setattr(rules_api, "forget_graph", lambda graph_uri: None)
    monkeypatch.setattr(rules_api, "remember_rule_element_graph", lambda **kwargs: None)
    monkeypatch.setattr(
        rules_api,
        "fetch_graph_ttl",
        lambda dataset, graph_uri: '@prefix ex: <http://example.org/> .\nex:A ex:has "x" .',
    )
    monkeypatch.setattr(
        rules_api,
        "fetch_rule_ttl",
        lambda rule_id: (
            f"https://firebim.org/graphs/rulebook/{rule_id}",
            '@prefix ex: <http://example.org/> .\nex:A ex:has "x" .',
        ),
    )
    monkeypatch.setattr(
        rules_api,
        "decompile_ruleset_ttl",
        lambda ttl: {
            "id": "room_area_rule",
            "entry": "check_room_area",
            "elements": [
                {
                    "id": "check_room_area",
                    "element_type": "decision",
                    "target_class": "http://example.org/fbo/1.0/Room",
                    "property_path": "http://example.org/fbo/1.0/roomArea",
                    "operator": ">=",
                    "value": 12,
                    "true_path": "room_ok",
                    "false_path": "room_fail",
                    "unknown_path": "room_unknown",
                    "label": "Check room area",
                }
            ],
        },
    )
    monkeypatch.setattr(
        rules_api,
        "decompile_rule_element_ttl",
        lambda ttl: {
            "id": "check_room_area",
            "element_type": "decision",
            "target_class": "http://example.org/fbo/1.0/Room",
            "property_path": "http://example.org/fbo/1.0/roomArea",
            "operator": ">=",
            "value": 12,
            "true_path": "room_ok",
            "false_path": "room_fail",
            "unknown_path": "room_unknown",
            "label": "Check room area",
        },
    )
    monkeypatch.setattr(
        rules_api,
        "list_rule_graphs",
        lambda: [
            {
                "rule_id": "room_area_rule",
                "graph_uri": "https://firebim.org/graphs/rulebook/room_area_rule",
                "entry": "check_room_area",
                "label": "Check room area",
                "element_count": 1,
            }
        ],
    )
    monkeypatch.setattr(
        rules_api,
        "list_rule_element_entries",
        lambda: [
            {
                "element_id": "check_room_area",
                "graph_uri": "https://firebim.org/graphs/rule-elements/check_room_area",
                "element_type": "decision",
                "label": "Check room area",
            }
        ],
    )
    monkeypatch.setattr(
        rules_api,
        "connect_ruleset_nodes",
        lambda ruleset, source_id, branch, target_id: {
            "updated": True,
            "overwritten": False,
            "previous_target_id": None,
            "ruleset": ruleset,
        },
    )
    monkeypatch.setattr(
        rules_api,
        "disconnect_ruleset_node_branch",
        lambda ruleset, source_id, branch: {
            "updated": True,
            "previous_target_id": "room_fail",
            "ruleset": ruleset,
        },
    )
    monkeypatch.setattr(
        rules_api,
        "check_ruleset_completeness",
        lambda ruleset: {
            "complete": True,
            "open": False,
            "broken": False,
            "entry_ok": True,
            "missing_entry": False,
            "missing_nodes": [],
            "open_branches": [],
            "errors": [],
        },
    )
    monkeypatch.setattr(
        rules_api,
        "force_end_ruleset",
        lambda ruleset, mode, node_type, label_prefix: {
            "updated": True,
            "filled_count": 2,
            "created_node_ids": ["forced_end_1", "forced_end_2"],
            "ruleset": ruleset,
        },
    )

    # Checks API
    monkeypatch.setattr(
        checks_api.CheckSolverSelector, "build_engine", lambda self, requested_mode=None, **kwargs: DummyCheckEngine()
    )
    monkeypatch.setattr(
        checks_api.CheckSolverSelector,
        "resolve_mode",
        lambda self, requested_mode=None: requested_mode or "standard",
    )
    monkeypatch.setattr(checks_api, "BlindShaclRuleRunner", DummyBlindRunner)
    monkeypatch.setattr(checks_api, "_published_ontology_summaries", lambda: [])
    monkeypatch.setattr(
        checks_api,
        "fetch_rule_ttl",
        lambda rule_id: (
            f"https://firebim.org/graphs/rulebook/{rule_id}",
            (
                "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
                "@prefix fb: <http://example.org/firebim/result/> .\n"
                "@prefix fbo: <http://example.org/fbo/1.0/> .\n"
                "@prefix ex: <http://example.org/rules/> .\n"
                "\n"
                "ex:Shape a sh:NodeShape ;\n"
                "    sh:targetClass fbo:Room ;\n"
                "    sh:path fbo:roomArea ;\n"
                '    sh:construct "CONSTRUCT { ?this <http://example.org/firebim/result> fb:True . } '
                'WHERE { ?this fbo:roomArea ?h . FILTER(?h >= 12) }" .\n'
            ),
        ),
    )
    monkeypatch.setattr(
        checks_api,
        "fetch_building_graph_ttl",
        lambda graph_uri: (
            """
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix fbo: <http://example.org/fbo/1.0/> .
@prefix ex: <http://example.org/building/> .

ex:room1 rdf:type fbo:Room ;
    fbo:roomArea 25 .
"""
        ),
    )
    monkeypatch.setattr(
        checks_api, "default_building_graph_uri", lambda graph_id: f"https://firebim.org/graphs/buildings/{graph_id}"
    )

    from app.storage.building_repository import BuildingRepository

    monkeypatch.setattr(
        BuildingRepository,
        "fetch_graph",
        lambda self, graph_id: (
            checks_api.default_building_graph_uri(graph_id),
            checks_api.fetch_building_graph_ttl(checks_api.default_building_graph_uri(graph_id)),
        ),
    )
    return authenticated_client_factory(app)


@pytest.fixture()
def sample_rule_payload():
    return {
        "rule": {
            "id": "room_area_rule",
            "entry": "check_room_area",
            "elements": [
                {
                    "id": "check_room_area",
                    "element_type": "decision",
                    "target_class": "http://example.org/fbo/1.0/Room",
                    "property_path": "http://example.org/fbo/1.0/roomArea",
                    "operator": ">=",
                    "value": 12,
                    "true_path": "room_ok",
                    "false_path": "room_fail",
                    "unknown_path": "room_unknown",
                    "label": "Check room area",
                    "config": {},
                }
            ],
        }
    }


@pytest.fixture()
def sample_rule_element_payload():
    return {
        "id": "check_room_area",
        "element_type": "decision",
        "target_class": "http://example.org/fbo/1.0/Room",
        "property_path": "http://example.org/fbo/1.0/roomArea",
        "operator": ">=",
        "value": 12,
        "true_path": "room_ok",
        "false_path": "room_fail",
        "unknown_path": "room_unknown",
        "label": "Check room area",
        "config": {},
    }


def test_graphs_health(client):
    response = client.get("/graphs/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dataset"] == "buildings"


def test_graphs_list(client):
    response = client.get("/graphs")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["graph_uris"][0].endswith("/test-graph")


def test_graphs_publish_fetch_delete(client):
    publish = client.post(
        "/graphs/publish",
        json={"graph_id": "test-graph", "ttl": '@prefix ex: <http://example.org/> .\nex:A ex:has "x" .'},
    )
    assert publish.status_code == 200
    assert publish.json()["graph_uri"].endswith("/test-graph")

    fetch = client.get("/graphs/test-graph")
    assert fetch.status_code == 200
    assert fetch.json()["graph_id"] == "test-graph"

    delete = client.delete("/graphs/test-graph")
    assert delete.status_code == 200
    assert delete.json()["success"] is True


def test_graphs_extract_schema(client):
    response = client.post("/graphs/extract-schema", json={"graph_id": "test-graph"})
    assert response.status_code == 200
    body = response.json()
    assert body["graph_id"] == "test-graph"
    assert body["triple_count"] >= 1
    assert isinstance(body["class_counts"], dict)
    assert isinstance(body["property_counts"], dict)


def test_legacy_graph_routes_removed(client):
    response = client.get("/graph/count_fro/dk")
    assert response.status_code == 404


def test_rules_health(client):
    response = client.get("/rules/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["operator"] == ">"


def test_rules_validate_compile_decompile(client, sample_rule_payload):
    validate = client.post("/rules/validate", json=sample_rule_payload)
    assert validate.status_code == 200
    assert validate.json()["valid"] is True

    compile_response = client.post("/rules/compile", json=sample_rule_payload)
    assert compile_response.status_code == 200
    assert "ex:Rule" in compile_response.json()["ttl"]

    decompile = client.post("/rules/decompile", json={"ttl": '@prefix ex: <http://example.org/> .\nex:A ex:has "x" .'})
    assert decompile.status_code == 200
    assert decompile.json()["rule"]["id"] == "room_area_rule"


def test_rules_publish_list_fetch_delete_and_ttl(client, sample_rule_payload):
    publish = client.post("/rules/publish", json=sample_rule_payload)
    assert publish.status_code == 200
    assert publish.json()["published"] is True

    list_response = client.get("/rules/list")
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1

    fetch_post = client.post("/rules/fetch", json={"rule_id": "room_area_rule"})
    assert fetch_post.status_code == 200
    assert fetch_post.json()["graph_uri"].endswith("/room_area_rule")

    fetch_get = client.get("/rules/room_area_rule")
    assert fetch_get.status_code == 200
    assert fetch_get.json()["rule"]["id"] == "room_area_rule"

    ttl_raw = client.get("/rules/room_area_rule/ttl")
    assert ttl_raw.status_code == 200
    assert "text/turtle" in ttl_raw.headers["content-type"]

    ttl_json = client.get("/rules/room_area_rule/ttl.json")
    assert ttl_json.status_code == 200
    assert ttl_json.json()["graph_uri"].endswith("/room_area_rule")

    delete = client.post("/rules/delete", json={"rule_id": "room_area_rule"})
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True


def test_rule_terms_reports_unique_terms_and_stats(client, monkeypatch):
    monkeypatch.setattr(
        rules_api,
        "_fetch_rule_by_id",
        lambda rule_id: SimpleNamespace(
            rule={
                "id": rule_id,
                "entry": "check_window",
                "elements": [
                    {
                        "id": "check_window_ifc",
                        "element_type": "decision",
                        "target_class": "https://firebim.org/ontologies/ifc/global/2026.04#IfcWindow",
                        "property_path": "https://firebim.org/ontologies/fbo/dk/2026.04#FireRating",
                    },
                    {
                        "id": "check_window_custom",
                        "element_type": "decision",
                        "target_class": "https://firebim.org/ontologies/custom/dk/2026.05#IfcWindow",
                        "property_path": "https://firebim.org/ontologies/custom/dk/2026.05#FireRating",
                    },
                    {
                        "id": "check_window_ifc_duplicate",
                        "element_type": "decision",
                        "target_class": "https://firebim.org/ontologies/ifc/global/2026.04#IfcWindow",
                        "property_path": "https://firebim.org/ontologies/fbo/dk/2026.04#FireRating",
                    },
                    {
                        "id": "check_property_only",
                        "element_type": "decision",
                        "property_path": "https://firebim.org/ontologies/fbo/dk/2026.04#FireExit",
                    },
                ],
            }
        ),
    )

    response = client.get("/rules/rule_terms_demo/terms")
    assert response.status_code == 200
    body = response.json()

    assert body["rule_id"] == "rule_terms_demo"
    assert body["compact_terms"] is False
    assert [item["full_iri"] for item in body["target_classes"]] == [
        "https://firebim.org/ontologies/custom/dk/2026.05#IfcWindow",
        "https://firebim.org/ontologies/ifc/global/2026.04#IfcWindow",
    ]
    assert [item["full_iri"] for item in body["property_paths"]] == [
        "https://firebim.org/ontologies/custom/dk/2026.05#FireRating",
        "https://firebim.org/ontologies/fbo/dk/2026.04#FireExit",
        "https://firebim.org/ontologies/fbo/dk/2026.04#FireRating",
    ]
    assert body["stats"]["element_count"] == 4
    assert body["stats"]["elements_with_target_class"] == 3
    assert body["stats"]["elements_with_property_path"] == 4
    assert body["stats"]["elements_with_both"] == 3
    assert body["stats"]["unique_target_classes"] == 2
    assert body["stats"]["unique_property_paths"] == 3
    assert body["stats"]["unique_target_property_pairs"] == 2
    assert body["stats"]["target_class_local_name_collisions"] == 1
    assert body["stats"]["property_path_local_name_collisions"] == 1
    assert body["target_class_local_name_groups"] == [
        {
            "local_name": "IfcWindow",
            "count": 2,
            "full_iris": [
                "https://firebim.org/ontologies/custom/dk/2026.05#IfcWindow",
                "https://firebim.org/ontologies/ifc/global/2026.04#IfcWindow",
            ],
            "namespaces": [
                "https://firebim.org/ontologies/custom/dk/2026.05",
                "https://firebim.org/ontologies/ifc/global/2026.04",
            ],
        }
    ]

    compact_response = client.get("/rules/rule_terms_demo/terms?compact_terms=true")
    assert compact_response.status_code == 200
    compact_body = compact_response.json()
    assert compact_body["compact_terms"] is True
    assert [item["value"] for item in compact_body["target_classes"]] == ["IfcWindow", "IfcWindow"]
    assert [item["value"] for item in compact_body["property_paths"]] == ["FireRating", "FireExit", "FireRating"]
    assert compact_body["target_property_pairs"][0]["compact_pair"].endswith("IfcWindow::FireRating")


def test_rule_terms_aggregate_reports_combined_unique_terms_and_stats(client, monkeypatch):
    rules = {
        "rule_a": {
            "id": "rule_a",
            "entry": "check_a",
            "elements": [
                {
                    "id": "a1",
                    "element_type": "decision",
                    "target_class": "https://firebim.org/ontologies/ifc/global/2026.04#IfcWindow",
                    "property_path": "https://firebim.org/ontologies/fbo/dk/2026.04#FireRating",
                },
                {
                    "id": "a2",
                    "element_type": "decision",
                    "property_path": "https://firebim.org/ontologies/fbo/dk/2026.04#FireExit",
                },
            ],
        },
        "rule_b": {
            "id": "rule_b",
            "entry": "check_b",
            "elements": [
                {
                    "id": "b1",
                    "element_type": "decision",
                    "target_class": "https://firebim.org/ontologies/custom/dk/2026.05#IfcWindow",
                    "property_path": "https://firebim.org/ontologies/custom/dk/2026.05#FireRating",
                },
                {
                    "id": "b2",
                    "element_type": "decision",
                    "target_class": "https://firebim.org/ontologies/ifc/global/2026.04#IfcDoor",
                    "property_path": "https://firebim.org/ontologies/fbo/dk/2026.04#FireRating",
                },
            ],
        },
    }
    monkeypatch.setattr(
        rules_api,
        "_fetch_rule_by_id",
        lambda rule_id: SimpleNamespace(rule=rules[rule_id]),
    )

    response = client.post(
        "/rules/terms/aggregate",
        json={"rule_ids": ["rule_a", "rule_b"], "compact_terms": True},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["rule_ids"] == ["rule_a", "rule_b"]
    assert body["compact_terms"] is True
    assert [item["full_iri"] for item in body["target_classes"]] == [
        "https://firebim.org/ontologies/custom/dk/2026.05#IfcWindow",
        "https://firebim.org/ontologies/ifc/global/2026.04#IfcDoor",
        "https://firebim.org/ontologies/ifc/global/2026.04#IfcWindow",
    ]
    assert body["stats"]["rule_count"] == 2
    assert body["stats"]["element_count"] == 4
    assert body["stats"]["rules_with_target_classes"] == 2
    assert body["stats"]["rules_with_property_paths"] == 2
    assert body["stats"]["rules_with_both"] == 2
    assert body["stats"]["unique_target_classes"] == 3
    assert body["stats"]["unique_property_paths"] == 3
    assert body["stats"]["unique_target_property_pairs"] == 3
    assert body["stats"]["target_class_local_name_collisions"] == 1
    assert body["stats"]["property_path_local_name_collisions"] == 1
    assert body["target_class_local_name_groups"] == [
        {
            "local_name": "IfcDoor",
            "count": 1,
            "full_iris": ["https://firebim.org/ontologies/ifc/global/2026.04#IfcDoor"],
            "namespaces": ["https://firebim.org/ontologies/ifc/global/2026.04"],
        },
        {
            "local_name": "IfcWindow",
            "count": 2,
            "full_iris": [
                "https://firebim.org/ontologies/custom/dk/2026.05#IfcWindow",
                "https://firebim.org/ontologies/ifc/global/2026.04#IfcWindow",
            ],
            "namespaces": [
                "https://firebim.org/ontologies/custom/dk/2026.05",
                "https://firebim.org/ontologies/ifc/global/2026.04",
            ],
        },
    ]


def test_rule_terms_aggregate_requires_two_rule_ids(client):
    response = client.post("/rules/terms/aggregate", json={"rule_ids": ["rule_a"]})
    assert response.status_code == 400
    assert response.json()["detail"] == "At least two rule_ids are required"


def test_rule_elements_publish_list_fetch_derive_delete(client, sample_rule_element_payload):
    publish = client.post("/rules/rule-elements", json=sample_rule_element_payload)
    assert publish.status_code == 200
    assert publish.json()["published"] is True

    list_response = client.get("/rules/rule-elements")
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1

    fetch_post = client.post("/rules/rule-elements/fetch", json={"element_id": "check_room_area"})
    assert fetch_post.status_code == 200
    assert fetch_post.json()["rule_element"]["id"] == "check_room_area"

    fetch_get = client.get("/rules/rule-elements/check_room_area")
    assert fetch_get.status_code == 200
    assert fetch_get.json()["rule_element"]["operator"] == ">="

    derive = client.post(
        "/rules/rule-elements/derive",
        json={
            "source_id": "check_room_area",
            "new_id": "check_room_area_copy",
            "preserve_paths": False,
            "overrides": {"label": "Derived rule element"},
        },
    )
    assert derive.status_code == 200
    body = derive.json()["rule_element"]
    assert body["id"] == "check_room_area_copy"
    assert body["label"] == "Derived rule element"
    assert body["true_path"] is None

    delete = client.post("/rules/rule-elements/delete", json={"element_id": "check_room_area"})
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True


def test_rules_assemble_connect_disconnect_completeness_and_force_end(client):
    assemble_payload = {
        "rule_id": "assembled_rule",
        "entry": "check_room_area",
        "element_ids": ["check_room_area"],
        "links": [
            {
                "source_id": "check_room_area",
                "branch": "true_path",
                "target_id": "room_ok",
            }
        ],
        "validate": True,
    }

    assemble = client.post("/rules/assemble", json=assemble_payload)
    assert assemble.status_code == 200
    assert assemble.json()["rule"]["id"] == "assembled_rule"

    assemble_compile = client.post("/rules/assemble/compile", json=assemble_payload)
    assert assemble_compile.status_code == 200
    assert "ttl" in assemble_compile.json()

    draft = {
        "id": "assembled_rule",
        "entry": "check_room_area",
        "elements": [
            {
                "id": "check_room_area",
                "element_type": "decision",
                "target_class": "http://example.org/fbo/1.0/Room",
                "property_path": "http://example.org/fbo/1.0/roomArea",
                "operator": ">=",
                "value": 12,
                "true_path": "room_ok",
                "false_path": "room_fail",
                "unknown_path": "room_unknown",
                "label": "Check room area",
                "config": {},
            }
        ],
    }

    connect = client.post(
        "/rules/connect",
        json={"rule": draft, "source_id": "check_room_area", "branch": "true_path", "target_id": "room_ok"},
    )
    assert connect.status_code == 200
    assert connect.json()["updated"] is True

    disconnect = client.post(
        "/rules/disconnect", json={"rule": draft, "source_id": "check_room_area", "branch": "true_path"}
    )
    assert disconnect.status_code == 200
    assert disconnect.json()["updated"] is True

    completeness = client.post("/rules/check-completeness", json={"rule": draft})
    assert completeness.status_code == 200
    assert completeness.json()["complete"] is True

    force_end = client.post(
        "/rules/force-end", json={"rule": draft, "mode": "per_branch", "node_type": "end", "label_prefix": "forced_end"}
    )
    assert force_end.status_code == 200
    assert force_end.json()["filled_count"] == 2


def test_checks_health(client):
    response = client.get("/checks/health")
    assert response.status_code == 200
    assert "standard" in response.json()["modes"]


def test_checks_run(client):
    response = client.post(
        "/checks/run",
        json={
            "rule_id": "room_area_rule",
            "graph_id": "building-a",
            "options": {"debug": True},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["passed"] == 1
    assert body["solver"] == "standard"
    assert body["engine"]["engine"] == "standard_compiled_runtime"
    assert body["start_node_id"] == "check_room_area"
    assert body["end_nodes"][0]["end_node_id"] == "room_ok"
    assert body["debug"]["rule_graph_uri"].endswith("/room_area_rule")


def test_checks_run_vertical(client):
    response = client.post("/checks/run-vertical", json={"rule_id": "room_area_rule", "graph_id": "building-a"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["solver"] == "vertical"


def test_checks_run_horisontal(client):
    response = client.post("/checks/run-horisontal", json={"rule_id": "room_area_rule", "graph_id": "building-a"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["solver"] == "horizontal"


def test_checks_run_diagnostic(client):
    response = client.post("/checks/run-diagnostic", json={"rule_id": "room_area_rule", "graph_id": "building-a"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["engine"]["diagnostics_enabled"] is True
    assert "timings" in body
    assert "completed_chains" in body


def test_checks_run_blind(client):
    response = client.post("/checks/run-blind", json={"rule_id": "room_area_rule", "graph_id": "building-a"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "blind_shacl_construct"
    assert body["terminal_counts"]["room_fail"] == 1
    assert body["end_outputs"][0]["terminal_node_id"] == "room_fail"
    assert body["debug"]["rule_graph_uri"].endswith("/room_area_rule")


def test_checks_compatibility(client):
    response = client.post("/checks/compatibility", json={"rule_id": "room_area_rule", "graph_id": "building-a"})
    assert response.status_code == 200
    body = response.json()
    assert body["compatibility_score"] == pytest.approx(1.0)
    assert "http://example.org/fbo/1.0/Room" in body["target_classes_required"]
    assert "http://example.org/fbo/1.0/roomArea" in body["properties_required"]
