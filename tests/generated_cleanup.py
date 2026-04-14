from __future__ import annotations

import os
from typing import Any

import httpx

API_BASE_URL = os.getenv("FIREBIM_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
HTTP_TIMEOUT_SECONDS = float(os.getenv("FIREBIM_HTTP_TIMEOUT", os.getenv("FIREBIM_API_TIMEOUT", "60")))

GENERATED_RULE_PREFIXES = (
    "it-rule-good-auto-small",
    "it-rule-unknown-auto-small",
    "it-rule-mismatch-auto-small",
    "it-rule-start-end-no-decision",
    "it-rule-delete-temp",
    "it-rule-assembled-",
    "it-rule-open-",
    "it-rule-missing-entry-",
    "it-rule-broken-ref-",
    "it-rule-compile-",
    "it-rule-complex-large-current-graph-",
)

GENERATED_RULE_ELEMENT_PREFIXES = (
    "it-element-auto-decision",
    "it-element-auto-decision-derived",
    "it-element-delete-temp",
    "it-el-decision-",
    "it-el-pass-",
    "it-el-fail-",
    "it-el-unknown-",
    "it-rule-complex-large-current-graph-",
)

TRUTHY_VALUES = {"1", "true", "yes", "on"}


def should_cleanup_generated_data() -> bool:
    return str(os.getenv("FIREBIM_CLEANUP_GENERATED", "")).strip().lower() in TRUTHY_VALUES


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{API_BASE_URL}{path}"
    timeout = httpx.Timeout(connect=10.0, read=HTTP_TIMEOUT_SECONDS, write=10.0, pool=10.0)
    with httpx.Client(timeout=timeout) as client:
        return client.request(method, url, **kwargs)


def _matches_prefix(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def list_generated_rules() -> list[str]:
    response = _request("GET", "/rules/list")
    response.raise_for_status()
    body = response.json()
    return sorted(
        item["rule_id"]
        for item in body.get("rules", [])
        if item.get("rule_id") and _matches_prefix(item["rule_id"], GENERATED_RULE_PREFIXES)
    )


def list_generated_rule_elements() -> list[str]:
    response = _request("GET", "/rules/rule-elements")
    response.raise_for_status()
    body = response.json()
    return sorted(
        item["element_id"]
        for item in body.get("rule_elements", [])
        if item.get("element_id") and _matches_prefix(item["element_id"], GENERATED_RULE_ELEMENT_PREFIXES)
    )


def delete_rule(rule_id: str) -> dict[str, Any]:
    response = _request("POST", "/rules/delete", json={"rule_id": rule_id})
    response.raise_for_status()
    return response.json()


def delete_rule_element(element_id: str) -> dict[str, Any]:
    response = _request("POST", "/rules/rule-elements/delete", json={"element_id": element_id})
    response.raise_for_status()
    return response.json()


def cleanup_generated_rulebook(*, verbose: bool = True) -> dict[str, list[str]]:
    deleted_rules: list[str] = []
    deleted_rule_elements: list[str] = []
    failed_rules: list[str] = []
    failed_rule_elements: list[str] = []

    for rule_id in list_generated_rules():
        try:
            body = delete_rule(rule_id)
            if body.get("deleted") is True:
                deleted_rules.append(rule_id)
            else:
                failed_rules.append(rule_id)
        except Exception:
            failed_rules.append(rule_id)

    for element_id in list_generated_rule_elements():
        try:
            body = delete_rule_element(element_id)
            if body.get("deleted") is True:
                deleted_rule_elements.append(element_id)
            else:
                failed_rule_elements.append(element_id)
        except Exception:
            failed_rule_elements.append(element_id)

    if verbose:
        print("Generated rule cleanup summary")
        print(f"  deleted_rules={len(deleted_rules)}")
        print(f"  deleted_rule_elements={len(deleted_rule_elements)}")
        if failed_rules:
            print(f"  failed_rules={failed_rules}")
        if failed_rule_elements:
            print(f"  failed_rule_elements={failed_rule_elements}")

    return {
        "deleted_rules": deleted_rules,
        "deleted_rule_elements": deleted_rule_elements,
        "failed_rules": failed_rules,
        "failed_rule_elements": failed_rule_elements,
    }
