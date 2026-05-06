from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

combo = import_module("tests.test_complex_rule_combo_integration")
generated_data_ops = import_module("tests.generated_data_ops")
delete_rule = generated_data_ops.delete_rule
delete_rule_element = generated_data_ops.delete_rule_element

RUN_MATRIX = [
    {
        "label": "standard-simple",
        "endpoint": "/checks/run",
        "solver": "standard",
        "diagnostics": False,
        "request_name": "run standard simple checks on large graph",
    },
    {
        "label": "horizontal-simple",
        "endpoint": "/checks/run-horisontal",
        "solver": "horizontal",
        "diagnostics": False,
        "request_name": "run horizontal simple checks on large graph",
    },
    {
        "label": "vertical-simple",
        "endpoint": "/checks/run-vertical",
        "solver": "vertical",
        "diagnostics": False,
        "request_name": "run vertical simple checks on large graph",
    },
    {
        "label": "standard-diagnostic",
        "endpoint": "/checks/run-diagnostic",
        "solver": "standard",
        "diagnostics": True,
        "request_name": "run standard diagnostic checks on large graph",
    },
    {
        "label": "horizontal-diagnostic",
        "endpoint": "/checks/run-diagnostic",
        "solver": "horizontal",
        "diagnostics": True,
        "request_name": "run horizontal diagnostic checks on large graph",
    },
    {
        "label": "vertical-diagnostic",
        "endpoint": "/checks/run-diagnostic",
        "solver": "vertical",
        "diagnostics": True,
        "request_name": "run vertical diagnostic checks on large graph",
    },
]


def _should_purge_after_run(argv: list[str]) -> bool:
    return "--cleanup-generated" in argv


def _build_run_options(*, solver: str, diagnostics: bool) -> dict[str, Any]:
    options = {
        "inference": "rdfs",
        "advanced": True,
        "include_report_text": True,
        "include_report_graph_ttl": False,
        "include_targets": diagnostics,
        "include_trace": diagnostics,
        "include_end_node_targets": False,
        "debug": diagnostics,
        "include_planner_debug": diagnostics,
        "fail_fast": False,
        "max_steps_per_target": 100,
        "solver": solver,
    }
    return options


def _assert_run_response(body: dict[str, Any], *, rule_id: str, solver: str, diagnostics: bool) -> None:
    assert body["success"] is True, body
    assert body["rule_id"] == rule_id, body
    assert body["graph_id"] == combo.GRAPH_ID, body
    assert body["solver"] == solver, body
    assert body["engine"]["engine"] == combo.EXPECTED_ENGINE_BY_SOLVER[solver], body
    assert body["engine"]["inference"] == "rdfs", body
    assert body["engine"]["advanced"] is True, body
    assert body["engine"]["diagnostics_enabled"] is diagnostics, body

    summary = body["summary"]
    assert summary["total_targets"] >= 1, body
    assert summary["passed"] + summary["failed"] + summary["unknown"] == summary["total_targets"], body

    compatibility = body["compatibility"]
    assert compatibility["compatibility_score"] == combo.pytest.approx(1.0), body
    assert compatibility["missing_target_classes"] == [], body
    assert compatibility["missing_properties"] == [], body


def _discover_large_graph_data() -> dict[str, Any]:
    graph = combo._parse_large_graph()
    schema_payload = combo._extract_large_graph_schema()

    available_specs = []
    missing_property_uris = []

    started = combo._now()
    for spec in combo.CORE_PROPERTY_SPECS:
        if combo._property_exists(graph, spec["property_uri"]):
            target_class = combo._discover_target_class_for_property(graph, spec["property_uri"])
            available_specs.append({**spec, "target_class": target_class})
        else:
            missing_property_uris.append(spec["property_uri"])
    discovery_ms = combo._elapsed_ms(started)

    combo._banner("graph property discovery")
    print(f"discovery_elapsed_ms={discovery_ms:.1f}")
    print(f"available_specs_count={len(available_specs)}")
    print(f"missing_property_count={len(missing_property_uris)}")
    print("available_property_labels=", [spec["label"] for spec in available_specs])
    if missing_property_uris:
        print("missing_property_uris=", combo._pretty(missing_property_uris))

    return {
        "graph": graph,
        "schema_payload": schema_payload,
        "available_specs": available_specs,
        "missing_property_uris": missing_property_uris,
    }


def _publish_combo_rule(discovered_large_graph_data: dict[str, Any]) -> dict[str, Any]:
    available_specs = discovered_large_graph_data["available_specs"]
    if not available_specs:
        raise AssertionError("No expected properties were found in the large graph.")

    connected_specs, transition_notes = combo._best_connected_spec_chain(
        available_specs,
        discovered_large_graph_data["schema_payload"],
    )
    if len(connected_specs) < 2:
        raise AssertionError("Could not derive a connected property/class chain from the large graph schema.")

    rule_id = combo._uid("it-rule-complex-large-current-graph")
    pass_id = f"{rule_id}-pass"
    fail_id = f"{rule_id}-fail"
    unknown_id = f"{rule_id}-unknown"

    decision_nodes = []
    element_ids = []

    combo._banner("connected combo chain")
    print(f"available_specs_count={len(available_specs)}")
    print(f"connected_specs_count={len(connected_specs)}")
    print("connected_property_labels=", [spec["label"] for spec in connected_specs])
    print("connected_transitions=", combo._pretty(transition_notes))

    for idx, spec in enumerate(connected_specs, start=1):
        node_id = f"{rule_id}-d{idx:02d}"
        next_node_id = f"{rule_id}-d{idx + 1:02d}" if idx < len(connected_specs) else pass_id
        node = combo._decision_node(node_id, spec["target_class"], spec, next_node_id)
        decision_nodes.append(node)
        element_ids.append(node_id)

    end_nodes = [
        combo._end_node(pass_id, "assembled-pass", "passed"),
        combo._end_node(fail_id, "assembled-fail", "failed"),
        combo._end_node(unknown_id, "assembled-unknown", "unknown"),
    ]
    element_ids.extend([pass_id, fail_id, unknown_id])

    combo._banner("publish rule elements")
    print(f"rule_id={rule_id}")
    print(f"decision_node_count={len(decision_nodes)}")
    print(f"end_node_count={len(end_nodes)}")

    publish_started = combo._now()
    for index, node in enumerate(decision_nodes + end_nodes, start=1):
        node_started = combo._now()
        res = combo._request("POST", "/rules/rule-elements", json=node)
        node_elapsed_ms = combo._elapsed_ms(node_started)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["published"] is True, body
        print(
            f"[publish {index:02d}/{len(decision_nodes) + len(end_nodes):02d}] "
            f"id={node['id']} type={node['element_type']} elapsed_ms={node_elapsed_ms:.1f}"
        )
    total_publish_ms = combo._elapsed_ms(publish_started)
    print(f"total_rule_element_publish_elapsed_ms={total_publish_ms:.1f}")

    assembled_rule = {
        "id": rule_id,
        "entry": decision_nodes[0]["id"],
        "elements": decision_nodes + end_nodes,
    }

    payload = {
        "rule_id": rule_id,
        "entry": decision_nodes[0]["id"],
        "element_ids": element_ids,
        "links": [],
        "validate": True,
    }
    res, took_ms = combo._timed_request("assemble rule", "POST", "/rules/assemble", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "assemble_response=",
        combo._pretty(
            {
                "elapsed_ms": round(took_ms, 1),
                "complete": body.get("complete"),
                "element_count": body.get("element_count"),
            }
        ),
    )
    assert body["complete"] is True, body

    res, took_ms = combo._timed_request(
        "check completeness before publish",
        "POST",
        "/rules/check-completeness",
        json={"rule": assembled_rule},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "completeness_before_publish=",
        combo._pretty(
            {"elapsed_ms": round(took_ms, 1), "complete": body.get("complete"), "errors": body.get("errors")}
        ),
    )
    assert body["complete"] is True, body

    res, took_ms = combo._timed_request("assemble compile rule", "POST", "/rules/assemble/compile", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "assemble_compile=",
        combo._pretty(
            {"elapsed_ms": round(took_ms, 1), "ttl_length": len(body.get("ttl", "")), "complete": body.get("complete")}
        ),
    )
    assert body["complete"] is True, body

    res, took_ms = combo._timed_request(
        "publish assembled rule",
        "POST",
        "/rules/publish",
        json={"rule": assembled_rule},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "publish_rule=",
        combo._pretty(
            {"elapsed_ms": round(took_ms, 1), "published": body.get("published"), "graph_uri": body.get("graph_uri")}
        ),
    )
    assert body["published"] is True, body

    res, took_ms = combo._timed_request(
        "compatibility check",
        "POST",
        "/checks/compatibility",
        json={"rule_id": rule_id, "graph_id": combo.GRAPH_ID},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "compatibility_response=",
        combo._pretty(
            {
                "elapsed_ms": round(took_ms, 1),
                "compatibility_score": body.get("compatibility_score"),
                "missing_target_classes": body.get("missing_target_classes"),
                "missing_properties": body.get("missing_properties"),
            }
        ),
    )
    assert body["compatibility_score"] == combo.pytest.approx(1.0), body
    assert body["missing_target_classes"] == [], body
    assert body["missing_properties"] == [], body

    return {
        "rule_id": rule_id,
        "entry": decision_nodes[0]["id"],
        "decision_nodes": decision_nodes,
        "end_nodes": end_nodes,
        "element_ids": element_ids,
        "available_specs": available_specs,
        "connected_specs": connected_specs,
        "transition_notes": transition_notes,
        "assembled_rule": assembled_rule,
    }


def _run_single_check(bundle: dict[str, Any], run_spec: dict[str, Any]) -> dict[str, Any]:
    options = _build_run_options(solver=run_spec["solver"], diagnostics=run_spec["diagnostics"])
    combo._banner("checks run configuration")
    print(f"base_url={combo.BASE_URL}")
    print(f"graph_id={combo.GRAPH_ID}")
    print(f"rule_id={bundle['rule_id']}")
    print(f"solver_requested={run_spec['solver']}")
    print(f"endpoint={run_spec['endpoint']}")
    print("options=", combo._pretty(options))

    res, took_ms = combo._timed_request(
        run_spec["request_name"],
        "POST",
        run_spec["endpoint"],
        json={
            "rule_id": bundle["rule_id"],
            "graph_id": combo.GRAPH_ID,
            "options": options,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()

    combo._print_check_response_stats(f"checks run response stats :: {run_spec['label']}", body, took_ms)
    _assert_run_response(
        body, rule_id=bundle["rule_id"], solver=run_spec["solver"], diagnostics=run_spec["diagnostics"]
    )

    summary = body["summary"]
    internal_run_ms = None
    if run_spec["diagnostics"]:
        internal_run_ms = (body.get("timings") or {}).get("request_ms", {}).get("run_rule")

    return {
        "label": run_spec["label"],
        "endpoint": run_spec["endpoint"],
        "solver": run_spec["solver"],
        "diagnostics": run_spec["diagnostics"],
        "request_ms": took_ms,
        "internal_run_ms": internal_run_ms,
        "total_targets": summary["total_targets"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "unknown": summary["unknown"],
        "conforms": summary["conforms"],
    }


def _purge_bundle(bundle: dict[str, Any]) -> None:
    print("\nPurging generated combo rule data ...")
    try:
        delete_rule(bundle["rule_id"])
    except Exception as exc:
        print(f"Failed to delete generated combo rule '{bundle['rule_id']}': {exc}")
    for element_id in bundle["element_ids"]:
        try:
            delete_rule_element(element_id)
        except Exception as exc:
            print(f"Failed to delete generated combo rule element '{element_id}': {exc}")


def _print_results_table(results: list[dict[str, Any]]) -> None:
    rows = []
    for item in results:
        rows.append(
            {
                "Run": item["label"],
                "Endpoint": item["endpoint"],
                "Solver": item["solver"],
                "Diag": "yes" if item["diagnostics"] else "no",
                "HTTP ms": f"{item['request_ms']:.1f}",
                "Run ms": f"{item['internal_run_ms']:.3f}" if item["internal_run_ms"] is not None else "-",
                "Targets": str(item["total_targets"]),
                "Pass": str(item["passed"]),
                "Fail": str(item["failed"]),
                "Unknown": str(item["unknown"]),
                "Conforms": "true" if item["conforms"] else "false",
            }
        )

    headers = list(rows[0].keys()) if rows else []
    widths = {header: max(len(header), max(len(row[header]) for row in rows)) for header in headers}

    combo._banner("combo run timing table")
    header_line = " | ".join(header.ljust(widths[header]) for header in headers)
    separator_line = "-+-".join("-" * widths[header] for header in headers)
    print(header_line)
    print(separator_line)
    for row in rows:
        print(" | ".join(row[header].ljust(widths[header]) for header in headers))


def main() -> int:
    purge_enabled = _should_purge_after_run(sys.argv[1:])
    print("FireBIM complex combo integration runner")
    print(f"API base URL: {combo.BASE_URL.rstrip('/')}")
    print(f"Generated-data purge after run: {'enabled' if purge_enabled else 'disabled'}")

    bundle: dict[str, Any] | None = None
    results: list[dict[str, Any]] = []
    exit_code = 0

    try:
        discovered = _discover_large_graph_data()
        bundle = _publish_combo_rule(discovered)

        for run_spec in RUN_MATRIX:
            results.append(_run_single_check(bundle, run_spec))

        _print_results_table(results)
        print("\nAll complex combo integration runs passed.")
        return 0
    except Exception:
        exit_code = 1
        raise
    finally:
        if purge_enabled and exit_code == 0 and bundle is not None:
            _purge_bundle(bundle)


if __name__ == "__main__":
    raise SystemExit(main())
