from __future__ import annotations

import os
import time
from typing import Any

import httpx
import pytest
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF

API_BASE_URL = os.getenv("FIREBIM_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = float(os.getenv("FIREBIM_API_TIMEOUT", "20"))
EVENTUAL_CONSISTENCY_TIMEOUT = float(os.getenv("FIREBIM_EVENTUAL_CONSISTENCY_TIMEOUT", "5"))
EVENTUAL_CONSISTENCY_INTERVAL = float(os.getenv("FIREBIM_EVENTUAL_CONSISTENCY_INTERVAL", "0.25"))

FBO = Namespace("http://example.org/fbo/1.0/")

GRAPH_IDS = {
    "small": "ifc-inspired-small",
    "medium": "ifc-inspired-medium",
    "large": "ifc-inspired-large",
}

RULE_IDS = {
    "good": "it-rule-good-auto-small",
    "unknown": "it-rule-unknown-auto-small",
    "nonconditional": "it-rule-start-end-no-decision",
    "delete_temp": "it-rule-delete-temp",
}

RULE_ELEMENT_IDS = {
    "decision": "it-element-auto-decision",
    "derived": "it-element-auto-decision-derived",
    "delete_temp": "it-element-delete-temp",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FIREBIM_RUN_INTEGRATION") != "1",
        reason="requires a running FireBIM API stack; set FIREBIM_RUN_INTEGRATION=1 to enable",
    ),
]


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=API_BASE_URL, timeout=TIMEOUT) as client:
        return client.request(method, path, **kwargs)


def _wait_for_rule_element_in_list(element_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + EVENTUAL_CONSISTENCY_TIMEOUT
    last_body: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        response = _request("GET", "/rules/rule-elements")
        response.raise_for_status()
        body = response.json()
        last_body = body
        returned_ids = {item["element_id"] for item in body.get("rule_elements", [])}
        if element_id in returned_ids:
            return body
        time.sleep(EVENTUAL_CONSISTENCY_INTERVAL)

    return last_body or {"rule_elements": []}


def _fetch_graph_ttl(graph_id: str) -> str:
    response = _request("GET", f"/graphs/{graph_id}")
    response.raise_for_status()
    body = response.json()
    assert body["success"] is True, body
    return body["ttl"]


def _parse_graph(ttl: str) -> Graph:
    graph = Graph()
    graph.parse(data=ttl, format="turtle")
    return graph


def _to_python_value(value: Any) -> Any:
    if isinstance(value, Literal):
        return value.toPython()
    return value


def _infer_rule_candidate(graph: Graph) -> dict[str, Any]:
    preferred = [
        ("SensorTypeSmokeSensor", "HasBuiltInAlarm"),
        ("Window", "FireExit"),
        ("Wall", "FireRating"),
        ("Room", "roomArea"),
        ("Building", "Name"),
        ("Site", "Name"),
    ]

    for class_name, prop_name in preferred:
        class_uri = FBO[class_name]
        prop_uri = FBO[prop_name]
        for subject in graph.subjects(RDF.type, class_uri):
            value = graph.value(subject, prop_uri)
            if value is not None:
                return {
                    "target_class": str(class_uri),
                    "property_path": str(prop_uri),
                    "value": _to_python_value(value),
                    "class_name": class_name,
                    "property_name": prop_name,
                }

    raise AssertionError(
        "Could not infer a usable rule candidate from the small building graph. "
        "Expected at least one of the preferred FBO class/property combinations."
    )


def _end_node(node_id: str, text: str, label: str | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "element_type": "end",
        "label": label or text,
        "config": {
            "output_text": text,
            "end_kind": "terminal",
        },
    }


def _good_rule_payload(candidate: dict[str, Any], rule_id: str) -> dict[str, Any]:
    decision_id = f"{rule_id}-decision"
    return {
        "rule": {
            "id": rule_id,
            "entry": decision_id,
            "elements": [
                {
                    "id": decision_id,
                    "element_type": "decision",
                    "target_class": candidate["target_class"],
                    "property_path": candidate["property_path"],
                    "operator": "==",
                    "value": candidate["value"],
                    "true_path": f"{rule_id}-pass",
                    "false_path": f"{rule_id}-fail",
                    "unknown_path": f"{rule_id}-unknown",
                    "label": f"{candidate['class_name']}.{candidate['property_name']} equals fixture value",
                    "config": {},
                },
                _end_node(f"{rule_id}-pass", "passed", "passed"),
                _end_node(f"{rule_id}-fail", "failed", "failed"),
                _end_node(f"{rule_id}-unknown", "missing data", "missing data"),
            ],
        }
    }


def _unknown_rule_payload(candidate: dict[str, Any], rule_id: str) -> dict[str, Any]:
    decision_id = f"{rule_id}-decision"
    return {
        "rule": {
            "id": rule_id,
            "entry": decision_id,
            "elements": [
                {
                    "id": decision_id,
                    "element_type": "decision",
                    "target_class": candidate["target_class"],
                    "property_path": str(FBO["DefinitelyMissingPropertyForIntegrationTest"]),
                    "operator": "==",
                    "value": candidate["value"],
                    "true_path": f"{rule_id}-pass",
                    "false_path": f"{rule_id}-fail",
                    "unknown_path": f"{rule_id}-unknown",
                    "label": "Unknown-path integration rule",
                    "config": {},
                },
                _end_node(f"{rule_id}-pass", "passed", "passed"),
                _end_node(f"{rule_id}-fail", "failed", "failed"),
                _end_node(f"{rule_id}-unknown", "missing data", "missing data"),
            ],
        }
    }


def _nonconditional_rule_payload(rule_id: str) -> dict[str, Any]:
    return {
        "rule": {
            "id": rule_id,
            "entry": f"{rule_id}-start",
            "elements": [
                {
                    "id": f"{rule_id}-start",
                    "element_type": "start",
                    "true_path": f"{rule_id}-end",
                    "label": "start",
                    "config": {},
                },
                _end_node(f"{rule_id}-end", "passed", "passed"),
            ],
        }
    }


def _invalid_rule_payload(rule_id: str) -> dict[str, Any]:
    missing_entry = f"{rule_id}-missing-entry"
    actual_decision = f"{rule_id}-decision"

    return {
        "rule": {
            "id": rule_id,
            "entry": missing_entry,  # bevidst forkert: entry peger ikke på en eksisterende node
            "elements": [
                {
                    "id": actual_decision,
                    "element_type": "decision",
                    "target_class": str(FBO["Room"]),
                    "property_path": str(FBO["roomArea"]),
                    "operator": ">",
                    "value": 10,
                    "true_path": f"{rule_id}-pass",
                    "false_path": f"{rule_id}-fail",
                    "unknown_path": f"{rule_id}-unknown",
                    "label": "Invalid integration rule: missing entry target",
                    "config": {},
                },
                _end_node(f"{rule_id}-pass", "passed", "passed"),
                _end_node(f"{rule_id}-fail", "failed", "failed"),
                _end_node(f"{rule_id}-unknown", "missing data", "missing data"),
            ],
        }
    }


def _ensure_rule_present(rule_payload: dict[str, Any]) -> dict[str, Any]:
    rule_id = rule_payload["rule"]["id"]
    fetch_response = _request("GET", f"/rules/{rule_id}")
    if fetch_response.status_code == 200:
        return fetch_response.json()

    publish_response = _request("POST", "/rules/publish", json=rule_payload)
    publish_response.raise_for_status()
    publish_body = publish_response.json()
    assert publish_body["published"] is True, publish_body

    fetch_response = _request("GET", f"/rules/{rule_id}")
    fetch_response.raise_for_status()
    return fetch_response.json()


def _ensure_rule_element_present(payload: dict[str, Any]) -> dict[str, Any]:
    element_id = payload["id"]
    fetch_response = _request("GET", f"/rules/rule-elements/{element_id}")
    if fetch_response.status_code == 200:
        return fetch_response.json()

    publish_response = _request("POST", "/rules/rule-elements", json=payload)
    publish_response.raise_for_status()
    publish_body = publish_response.json()
    assert publish_body["published"] is True, publish_body

    fetch_response = _request("GET", f"/rules/rule-elements/{element_id}")
    fetch_response.raise_for_status()
    return fetch_response.json()


@pytest.fixture(scope="session")
def candidate() -> dict[str, Any]:
    ttl = _fetch_graph_ttl(GRAPH_IDS["small"])
    graph = _parse_graph(ttl)
    return _infer_rule_candidate(graph)


@pytest.fixture(scope="session")
def ensured_rules(candidate):
    return {
        "good": _ensure_rule_present(_good_rule_payload(candidate, RULE_IDS["good"])),
        "unknown": _ensure_rule_present(_unknown_rule_payload(candidate, RULE_IDS["unknown"])),
        "nonconditional": _ensure_rule_present(_nonconditional_rule_payload(RULE_IDS["nonconditional"])),
    }


@pytest.fixture(scope="session")
def ensured_rule_element(candidate):
    payload = {
        "id": RULE_ELEMENT_IDS["decision"],
        "element_type": "decision",
        "target_class": candidate["target_class"],
        "property_path": candidate["property_path"],
        "operator": "==",
        "value": candidate["value"],
        "true_path": "integration_true_end",
        "false_path": "integration_false_end",
        "unknown_path": "integration_unknown_end",
        "label": "Atomic integration decision",
        "config": {},
    }
    return _ensure_rule_element_present(payload)


def test_rules_health():
    response = _request("GET", "/rules/health")
    response.raise_for_status()
    body = response.json()

    assert body["status"] == "ok"
    assert "rule_element_id" in body
    assert "operator" in body


def test_validate_good_rule(candidate):
    response = _request("POST", "/rules/validate", json=_good_rule_payload(candidate, "it-rule-validate-good"))
    response.raise_for_status()
    body = response.json()

    assert body["valid"] is True
    assert body["rule_id"] == "it-rule-validate-good"
    assert body["element_count"] == 4
    assert body["errors"] == []


def test_validate_invalid_rule_returns_validation_error():
    response = _request("POST", "/rules/validate", json=_invalid_rule_payload("it-rule-validate-invalid"))
    response.raise_for_status()
    body = response.json()

    assert body["valid"] is False
    assert body["rule_id"] == "it-rule-validate-invalid"
    assert body["errors"]


def test_compile_good_rule(candidate):
    response = _request("POST", "/rules/compile", json=_good_rule_payload(candidate, "it-rule-compile-good"))
    response.raise_for_status()
    body = response.json()

    assert body["rule_id"] == "it-rule-compile-good"
    assert body["element_count"] == 4
    assert "sh:NodeShape" in body["ttl"]
    assert "RuleSet" in body["ttl"]


def test_publish_fetch_and_list_rules(ensured_rules):
    good = ensured_rules["good"]
    assert good["success"] is True
    assert good["graph_uri"].endswith(f"/{RULE_IDS['good']}")
    assert good["rule"] is not None

    list_response = _request("GET", "/rules/list")
    list_response.raise_for_status()
    list_body = list_response.json()
    returned_ids = {item["rule_id"] for item in list_body.get("rules", [])}

    assert RULE_IDS["good"] in returned_ids
    assert RULE_IDS["unknown"] in returned_ids
    assert RULE_IDS["nonconditional"] in returned_ids


def test_get_rule_ttl_json_for_good_rule(ensured_rules):
    response = _request("GET", f"/rules/{RULE_IDS['good']}/ttl.json")
    response.raise_for_status()
    body = response.json()

    assert body["graph_uri"].endswith(f"/{RULE_IDS['good']}")
    assert "sh:NodeShape" in body["ttl"]


def test_publish_fetch_list_and_derive_rule_element(ensured_rule_element):
    assert ensured_rule_element["success"] is True
    assert ensured_rule_element["rule_element"] is not None
    assert ensured_rule_element["rule_element"]["id"] == RULE_ELEMENT_IDS["decision"]

    list_body = _wait_for_rule_element_in_list(RULE_ELEMENT_IDS["decision"])
    returned_ids = {item["element_id"] for item in list_body.get("rule_elements", [])}
    assert RULE_ELEMENT_IDS["decision"] in returned_ids

    derive_response = _request(
        "POST",
        "/rules/rule-elements/derive",
        json={
            "source_id": RULE_ELEMENT_IDS["decision"],
            "new_id": RULE_ELEMENT_IDS["derived"],
            "preserve_paths": False,
            "overrides": {"label": "Derived integration element"},
        },
    )
    derive_response.raise_for_status()
    derive_body = derive_response.json()

    assert derive_body["rule_element"]["id"] == RULE_ELEMENT_IDS["derived"]
    assert derive_body["rule_element"]["label"] == "Derived integration element"
    assert derive_body["rule_element"]["graph_uri"] is None


def test_delete_only_temp_rule(candidate):
    temp_payload = _good_rule_payload(candidate, RULE_IDS["delete_temp"])
    _ensure_rule_present(temp_payload)

    delete_response = _request("POST", "/rules/delete", json={"rule_id": RULE_IDS["delete_temp"]})
    delete_response.raise_for_status()
    delete_body = delete_response.json()

    assert delete_body["deleted"] is True
    assert delete_body["graph_uri"].endswith(f"/{RULE_IDS['delete_temp']}")

    fetch_response = _request("GET", f"/rules/{RULE_IDS['delete_temp']}")
    assert fetch_response.status_code in {404, 500}


def test_delete_only_temp_rule_element(candidate):
    temp_payload = {
        "id": RULE_ELEMENT_IDS["delete_temp"],
        "element_type": "decision",
        "target_class": candidate["target_class"],
        "property_path": candidate["property_path"],
        "operator": "==",
        "value": candidate["value"],
        "true_path": "temp_true_end",
        "false_path": "temp_false_end",
        "unknown_path": "temp_unknown_end",
        "label": "Temporary integration element",
        "config": {},
    }
    _ensure_rule_element_present(temp_payload)

    delete_response = _request(
        "POST",
        "/rules/rule-elements/delete",
        json={"element_id": RULE_ELEMENT_IDS["delete_temp"]},
    )
    delete_response.raise_for_status()
    delete_body = delete_response.json()

    assert delete_body["deleted"] is True
    assert delete_body["graph_uri"].endswith(f"/{RULE_ELEMENT_IDS['delete_temp']}")

    fetch_response = _request("GET", f"/rules/rule-elements/{RULE_ELEMENT_IDS['delete_temp']}")
    assert fetch_response.status_code in {404, 500}
