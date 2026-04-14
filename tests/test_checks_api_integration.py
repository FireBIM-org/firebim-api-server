import os
from typing import Any

import httpx
import pytest

BASE_URL = os.getenv("FIREBIM_API_BASE_URL", "http://127.0.0.1:8000")

FBO = "http://example.org/fbo/1.0/"
GRAPH_IDS = {
    "small": "ifc-inspired-small",
    "medium": "ifc-inspired-medium",
    "large": "ifc-inspired-large",
}
RULE_IDS = {
    "good": "it-rule-good-auto-small",
    "unknown": "it-rule-unknown-auto-small",
    "mismatch": "it-rule-mismatch-auto-small",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FIREBIM_RUN_INTEGRATION") != "1",
        reason="requires a running FireBIM API stack; set FIREBIM_RUN_INTEGRATION=1 to enable",
    ),
]


def _request(method: str, path: str, **kwargs):
    url = f"{BASE_URL}{path}"
    with httpx.Client(timeout=60.0) as client:
        return client.request(method, url, **kwargs)


def _end_node(node_id: str, label: str, terminal_value: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "element_type": "end",
        "label": label,
        "config": {
            "terminal_value": terminal_value,
        },
    }


def _good_rule_payload(rule_id: str) -> dict[str, Any]:
    return {
        "rule": {
            "id": rule_id,
            "entry": f"{rule_id}-decision",
            "elements": [
                {
                    "id": f"{rule_id}-decision",
                    "element_type": "decision",
                    "target_class": f"{FBO}SensorTypeSmokeSensor",
                    "property_path": f"{FBO}HasBuiltInAlarm",
                    "operator": "==",
                    "value": False,
                    "true_path": f"{rule_id}-pass",
                    "false_path": f"{rule_id}-fail",
                    "unknown_path": f"{rule_id}-unknown",
                    "label": "Smoke sensor built-in alarm equals false",
                    "config": {},
                },
                _end_node(f"{rule_id}-pass", "passed", "passed"),
                _end_node(f"{rule_id}-fail", "failed", "failed"),
                _end_node(f"{rule_id}-unknown", "unknown", "unknown"),
            ],
        }
    }


def _unknown_rule_payload(rule_id: str) -> dict[str, Any]:
    return {
        "rule": {
            "id": rule_id,
            "entry": f"{rule_id}-decision",
            "elements": [
                {
                    "id": f"{rule_id}-decision",
                    "element_type": "decision",
                    "target_class": f"{FBO}SensorTypeSmokeSensor",
                    "property_path": f"{FBO}DoesNotExistYet",
                    "operator": "==",
                    "value": True,
                    "true_path": f"{rule_id}-pass",
                    "false_path": f"{rule_id}-fail",
                    "unknown_path": f"{rule_id}-unknown",
                    "label": "Unknown property test",
                    "config": {},
                },
                _end_node(f"{rule_id}-pass", "passed", "passed"),
                _end_node(f"{rule_id}-fail", "failed", "failed"),
                _end_node(f"{rule_id}-unknown", "unknown", "unknown"),
            ],
        }
    }


def _mismatch_rule_payload(rule_id: str) -> dict[str, Any]:
    return {
        "rule": {
            "id": rule_id,
            "entry": f"{rule_id}-decision",
            "elements": [
                {
                    "id": f"{rule_id}-decision",
                    "element_type": "decision",
                    "target_class": f"{FBO}NonExistingType",
                    "property_path": f"{FBO}NonExistingProperty",
                    "operator": "==",
                    "value": True,
                    "true_path": f"{rule_id}-pass",
                    "false_path": f"{rule_id}-fail",
                    "unknown_path": f"{rule_id}-unknown",
                    "label": "Intentional mismatch rule",
                    "config": {},
                },
                _end_node(f"{rule_id}-pass", "passed", "passed"),
                _end_node(f"{rule_id}-fail", "failed", "failed"),
                _end_node(f"{rule_id}-unknown", "unknown", "unknown"),
            ],
        }
    }


@pytest.fixture(scope="module")
def ensured_rules():
    payloads = {
        "good": _good_rule_payload(RULE_IDS["good"]),
        "unknown": _unknown_rule_payload(RULE_IDS["unknown"]),
        "mismatch": _mismatch_rule_payload(RULE_IDS["mismatch"]),
    }
    out = {}
    for key, payload in payloads.items():
        res = _request("POST", "/rules/publish", json=payload)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["published"] is True, body
        out[key] = body
    return out


def _run_options() -> dict[str, Any]:
    return {
        "solver": "standard",
        "inference": "rdfs",
        "advanced": True,
        "include_report_text": True,
        "include_report_graph_ttl": False,
        "include_targets": False,
        "include_trace": False,
        "debug": False,
        "fail_fast": False,
        "max_steps_per_target": 100,
    }


def _assert_optional_report_text(body: dict[str, Any]) -> None:
    assert "report_text" in body
    assert body["report_text"] is None or isinstance(body["report_text"], str)


def _run_blind_options() -> dict[str, Any]:
    return {
        "include_targets": True,
        "include_debug": True,
        "max_steps": 10000,
    }


def test_checks_health():
    response = _request("GET", "/checks/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}


def test_compatibility_good_rule_small_graph(ensured_rules):
    response = _request(
        "POST",
        "/checks/compatibility",
        json={"rule_id": RULE_IDS["good"], "graph_id": GRAPH_IDS["small"]},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["rule_id"] == RULE_IDS["good"]
    assert body["graph_id"] == GRAPH_IDS["small"]
    assert body["compatibility_score"] == pytest.approx(1.0)
    assert body["missing_target_classes"] == []
    assert body["missing_properties"] == []


def test_compatibility_mismatch_rule_small_graph(ensured_rules):
    response = _request(
        "POST",
        "/checks/compatibility",
        json={"rule_id": RULE_IDS["mismatch"], "graph_id": GRAPH_IDS["small"]},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["rule_id"] == RULE_IDS["mismatch"]
    assert body["graph_id"] == GRAPH_IDS["small"]
    assert body["compatibility_score"] < 1.0
    assert len(body["missing_target_classes"]) >= 1 or len(body["missing_properties"]) >= 1


def test_run_blind_good_rule_currently_returns_known_fallback_or_success(ensured_rules):
    response = _request(
        "POST",
        "/checks/run-blind",
        json={
            "rule_id": RULE_IDS["good"],
            "graph_id": GRAPH_IDS["small"],
            "options": _run_blind_options(),
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert "success" in body
    assert "fallback" in body

    if body["success"] is False:
        assert body["fallback"] is True
        assert body["error"] == "blind_check_run_error"
    else:
        assert isinstance(body.get("end_outputs", []), list)


def test_run_blind_unknown_rule_currently_returns_known_fallback_or_success(ensured_rules):
    response = _request(
        "POST",
        "/checks/run-blind",
        json={
            "rule_id": RULE_IDS["unknown"],
            "graph_id": GRAPH_IDS["small"],
            "options": _run_blind_options(),
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert "success" in body
    assert "fallback" in body

    if body["success"] is False:
        assert body["fallback"] is True
        assert body["error"] == "blind_check_run_error"
    else:
        assert isinstance(body.get("end_outputs", []), list)


def test_run_standard_solver_good_rule_small_graph_validates_requested_output_contract(ensured_rules):
    response = _request(
        "POST",
        "/checks/run",
        json={
            "rule_id": RULE_IDS["good"],
            "graph_id": GRAPH_IDS["small"],
            "options": _run_options(),
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["success"] is True, body
    assert body["rule_id"] == RULE_IDS["good"]
    assert body["graph_id"] == GRAPH_IDS["small"]

    assert body["solver"] == "standard"
    assert body["start_node_id"] is not None
    assert body["engine"]["engine"] == "standard_compiled_runtime"
    assert body["engine"]["inference"] == "rdfs"
    assert body["engine"]["advanced"] is True
    assert body["engine"]["diagnostics_enabled"] is False

    assert "summary" in body
    assert "compatibility" in body
    assert "end_nodes" in body
    assert "end_node_targets" in body

    _assert_optional_report_text(body)
    assert isinstance(body["end_nodes"], list)
    assert isinstance(body["end_node_targets"], list)

    summary = body["summary"]
    assert summary["total_targets"] >= 1, body
    assert summary["passed"] + summary["failed"] + summary["unknown"] == summary["total_targets"], body
    assert sum(item["count"] for item in body["end_nodes"]) == summary["total_targets"]

    compatibility = body["compatibility"]
    assert compatibility["compatibility_score"] == pytest.approx(1.0)
    assert compatibility["missing_target_classes"] == []
    assert compatibility["missing_properties"] == []


def test_run_compiled_runtime_good_rule_medium_graph_expected_failed_targets_pattern(ensured_rules):
    response = _request(
        "POST",
        "/checks/run",
        json={
            "rule_id": RULE_IDS["good"],
            "graph_id": GRAPH_IDS["medium"],
            "options": _run_options(),
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["success"] is True, body
    assert body["solver"] == "standard"
    assert body["engine"]["engine"] == "standard_compiled_runtime"
    assert body["compatibility"]["compatibility_score"] == pytest.approx(1.0)
    assert body["compatibility"]["missing_target_classes"] == []
    assert body["compatibility"]["missing_properties"] == []

    summary = body["summary"]
    assert summary["total_targets"] >= 1, body
    assert summary["passed"] + summary["failed"] + summary["unknown"] == summary["total_targets"], body
    assert summary["passed"] == 0, body
    assert summary["unknown"] == 0, body
    assert summary["failed"] == summary["total_targets"], body


def test_run_compiled_runtime_unknown_rule_small_graph_exposes_requested_output_contract(ensured_rules):
    response = _request(
        "POST",
        "/checks/run",
        json={
            "rule_id": RULE_IDS["unknown"],
            "graph_id": GRAPH_IDS["small"],
            "options": _run_options(),
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["success"] is True, body
    assert body["solver"] == "standard"
    assert body["engine"]["engine"] == "standard_compiled_runtime"
    assert body["engine"]["inference"] == "rdfs"
    assert body["engine"]["advanced"] is True

    assert "summary" in body
    assert "compatibility" in body
    assert "end_nodes" in body
    assert "end_node_targets" in body

    _assert_optional_report_text(body)
    assert isinstance(body["end_nodes"], list)
    assert isinstance(body["end_node_targets"], list)

    summary = body["summary"]
    assert summary["total_targets"] >= 1, body
    assert summary["passed"] + summary["failed"] + summary["unknown"] == summary["total_targets"], body


def test_run_vertical_simple_endpoint_returns_vertical_solver(ensured_rules):
    response = _request(
        "POST",
        "/checks/run-vertical",
        json={
            "rule_id": RULE_IDS["good"],
            "graph_id": GRAPH_IDS["small"],
            "options": _run_options(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True, body
    assert body["solver"] == "vertical"
    assert body["engine"]["engine"] == "vertical_compiled_runtime"
    assert body["engine"]["diagnostics_enabled"] is False


def test_run_horisontal_simple_endpoint_returns_horizontal_solver(ensured_rules):
    response = _request(
        "POST",
        "/checks/run-horisontal",
        json={
            "rule_id": RULE_IDS["good"],
            "graph_id": GRAPH_IDS["small"],
            "options": _run_options(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True, body
    assert body["solver"] == "horizontal"
    assert body["engine"]["engine"] == "horizontal_compiled_runtime"
    assert body["engine"]["diagnostics_enabled"] is False


def test_run_diagnostic_exposes_timings_and_sequences(ensured_rules):
    response = _request(
        "POST",
        "/checks/run-diagnostic",
        json={
            "rule_id": RULE_IDS["good"],
            "graph_id": GRAPH_IDS["small"],
            "options": {**_run_options(), "debug": True},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True, body
    assert body["engine"]["diagnostics_enabled"] is True
    assert "timings" in body
    assert "sequence_events" in body
    assert "completed_chains" in body
