import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("FIREBIM_API_BASE_URL", "http://127.0.0.1:8000")
FBO = "http://example.org/fbo/1.0/"

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


def _unique_id(prefix: str):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _decision_element(element_id, true_id, false_id, unknown_id):
    return {
        "id": element_id,
        "element_type": "decision",
        "target_class": f"{FBO}Room",
        "property_path": f"{FBO}roomArea",
        "operator": ">",
        "value": 10,
        "true_path": true_id,
        "false_path": false_id,
        "unknown_path": unknown_id,
        "label": "Room area check",
        "config": {},
    }


def _end_node(element_id, label):
    return {
        "id": element_id,
        "element_type": "end",
        "label": label,
        "config": {},
    }


@pytest.fixture(scope="module")
def created_elements():
    ids = {
        "decision": _unique_id("it-el-decision"),
        "pass": _unique_id("it-el-pass"),
        "fail": _unique_id("it-el-fail"),
        "unknown": _unique_id("it-el-unknown"),
    }

    elements = [
        _decision_element(ids["decision"], ids["pass"], ids["fail"], ids["unknown"]),
        _end_node(ids["pass"], "passed"),
        _end_node(ids["fail"], "failed"),
        _end_node(ids["unknown"], "unknown"),
    ]

    for el in elements:
        res = _request("POST", "/rules/rule-elements", json=el)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["published"] is True

    return ids


def test_fetch_rule_elements(created_elements):
    for el_id in created_elements.values():
        res = _request("POST", "/rules/rule-elements/fetch", json={"element_id": el_id})
        assert res.status_code == 200
        body = res.json()
        assert body["rule_element"]["id"] == el_id


def test_assemble_complete_rule(created_elements):
    rule_id = _unique_id("it-rule-assembled")

    payload = {
        "rule_id": rule_id,
        "entry": created_elements["decision"],
        "element_ids": list(created_elements.values()),
        "links": [],
        "validate": True,
    }

    res = _request("POST", "/rules/assemble", json=payload)
    assert res.status_code == 200
    body = res.json()

    assert body["complete"] is True
    assert body["entry_ok"] is True
    assert body["broken"] is False
    assert body["open"] is False
    assert body["errors"] == []


def test_check_completeness_complete_rule(created_elements):
    rule = {
        "id": "test-rule",
        "entry": created_elements["decision"],
        "elements": [
            _decision_element(
                created_elements["decision"],
                created_elements["pass"],
                created_elements["fail"],
                created_elements["unknown"],
            ),
            _end_node(created_elements["pass"], "passed"),
            _end_node(created_elements["fail"], "failed"),
            _end_node(created_elements["unknown"], "unknown"),
        ],
    }

    res = _request("POST", "/rules/check-completeness", json={"rule": rule})
    assert res.status_code == 200
    body = res.json()

    assert body["complete"] is True
    assert body["broken"] is False
    assert body["open"] is False
    assert body["entry_ok"] is True
    assert body["missing_nodes"] == []
    assert body["open_branches"] == []
    assert body["errors"] == []


def test_check_completeness_detects_open_branches(created_elements):
    broken_decision = {
        "id": created_elements["decision"],
        "element_type": "decision",
        "target_class": f"{FBO}Room",
        "property_path": f"{FBO}roomArea",
        "operator": ">",
        "value": 10,
        "true_path": created_elements["pass"],
        "false_path": None,
        "unknown_path": None,
        "label": "Broken rule",
        "config": {},
    }

    res = _request(
        "POST",
        "/rules/check-completeness",
        json={
            "rule": {
                "id": _unique_id("it-rule-open"),
                "entry": created_elements["decision"],
                "elements": [
                    broken_decision,
                    _end_node(created_elements["pass"], "passed"),
                ],
            }
        },
    )
    assert res.status_code == 200
    body = res.json()

    assert body["complete"] is False
    assert body["open"] is True
    assert len(body["open_branches"]) >= 1


def test_check_completeness_detects_missing_entry_reference(created_elements):
    res = _request(
        "POST",
        "/rules/check-completeness",
        json={
            "rule": {
                "id": _unique_id("it-rule-missing-entry"),
                "entry": "missing-entry-node",
                "elements": [
                    _decision_element(
                        created_elements["decision"],
                        created_elements["pass"],
                        created_elements["fail"],
                        created_elements["unknown"],
                    ),
                    _end_node(created_elements["pass"], "passed"),
                    _end_node(created_elements["fail"], "failed"),
                    _end_node(created_elements["unknown"], "unknown"),
                ],
            }
        },
    )
    assert res.status_code == 200
    body = res.json()

    assert body["complete"] is False
    assert body["entry_ok"] is False


def test_check_completeness_detects_broken_reference(created_elements):
    broken_target = "missing-target-node"
    res = _request(
        "POST",
        "/rules/check-completeness",
        json={
            "rule": {
                "id": _unique_id("it-rule-broken-ref"),
                "entry": created_elements["decision"],
                "elements": [
                    {
                        "id": created_elements["decision"],
                        "element_type": "decision",
                        "target_class": f"{FBO}Room",
                        "property_path": f"{FBO}roomArea",
                        "operator": ">",
                        "value": 10,
                        "true_path": broken_target,
                        "false_path": created_elements["fail"],
                        "unknown_path": created_elements["unknown"],
                        "label": "Broken reference rule",
                        "config": {},
                    },
                    _end_node(created_elements["fail"], "failed"),
                    _end_node(created_elements["unknown"], "unknown"),
                ],
            }
        },
    )
    assert res.status_code == 200
    body = res.json()

    assert body["complete"] is False
    assert body["broken"] is True
    assert broken_target in body["missing_nodes"]


def test_assemble_compile_rule(created_elements):
    rule_id = _unique_id("it-rule-compile")

    payload = {
        "rule_id": rule_id,
        "entry": created_elements["decision"],
        "element_ids": list(created_elements.values()),
        "links": [],
        "validate": True,
    }

    res = _request("POST", "/rules/assemble/compile", json=payload)
    assert res.status_code == 200
    body = res.json()

    assert body["ttl"]
    assert "sh:" in body["ttl"]
