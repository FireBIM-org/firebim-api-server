import json
import os
import time
import uuid
from typing import Any

import httpx
import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from tests.generated_cleanup import delete_rule, delete_rule_element, should_cleanup_generated_data

BASE_URL = os.getenv("FIREBIM_API_BASE_URL", "http://127.0.0.1:8000")
GRAPH_ID = "ifc-inspired-large"
FBO = "http://example.org/fbo/1.0/"
TEST_SOLVER = os.getenv("FIREBIM_CHECKS_SOLVER") or os.getenv("CHECKS_SOLVER")
PRINT_JSON_INDENT = 2
HTTP_TIMEOUT_SECONDS = float(os.getenv("FIREBIM_HTTP_TIMEOUT", "300"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FIREBIM_RUN_INTEGRATION") != "1",
        reason="requires a running FireBIM API stack; set FIREBIM_RUN_INTEGRATION=1 to enable",
    ),
]

EXPECTED_ENGINE_BY_SOLVER = {
    "legacy_compiled": "standard_compiled_runtime",
    "standard": "standard_compiled_runtime",
    "horizontal_batch": "horizontal_compiled_runtime",
    "horizontal": "horizontal_compiled_runtime",
    "vertical_pathway": "vertical_compiled_runtime",
    "vertical": "vertical_compiled_runtime",
}


def _now() -> float:
    return time.perf_counter()


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _pretty(value: Any) -> str:
    try:
        return json.dumps(value, indent=PRINT_JSON_INDENT, sort_keys=True, default=str)
    except Exception:
        return repr(value)


def _banner(title: str) -> None:
    print(f"\n{'=' * 24} {title} {'=' * 24}")


def _request(method: str, path: str, **kwargs):
    url = f"{BASE_URL}{path}"
    timeout = httpx.Timeout(connect=30.0, read=HTTP_TIMEOUT_SECONDS, write=30.0, pool=30.0)
    with httpx.Client(timeout=timeout) as client:
        return client.request(method, url, **kwargs)


def _timed_request(label: str, method: str, path: str, **kwargs):
    _banner(f"{label} :: request")
    print(f"method={method} path={path}")
    started = _now()
    response = _request(method, path, **kwargs)
    took_ms = _elapsed_ms(started)
    print(f"status_code={response.status_code} elapsed_ms={took_ms:.1f}")
    return response, took_ms


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _end_node(node_id: str, label: str, terminal_value: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "element_type": "end",
        "label": label,
        "config": {"terminal_value": terminal_value},
    }


CORE_PROPERTY_SPECS = [
    {"label": "Building height", "property_uri": f"{FBO}BuildingHeight", "operator": ">", "value": 0},
    {"label": "Storey number", "property_uri": f"{FBO}StoreyNumber", "operator": ">=", "value": 1},
    {"label": "Fire rating", "property_uri": f"{FBO}FireRating", "operator": "==", "value": "EI60"},
    {"label": "Load bearing", "property_uri": f"{FBO}LoadBearing", "operator": "==", "value": True},
    {"label": "Number of occupants", "property_uri": f"{FBO}NumberOfOccupants", "operator": ">=", "value": 1},
    {"label": "Occupancy type", "property_uri": f"{FBO}OccupancyType", "operator": "==", "value": "office"},
    {"label": "Built-in alarm", "property_uri": f"{FBO}HasBuiltInAlarm", "operator": "==", "value": True},
    {
        "label": "Smoke detection coverage",
        "property_uri": f"{FBO}HasSmokeDetectionCoverage",
        "operator": "==",
        "value": True,
    },
    {"label": "Fire compartment id", "property_uri": f"{FBO}FireCompartmentId", "operator": "==", "value": "FC-1"},
    {"label": "Fire exit", "property_uri": f"{FBO}FireExit", "operator": "==", "value": True},
]


def _parse_large_graph() -> Graph:
    res, took_ms = _timed_request("fetch large graph", "GET", f"/graphs/{GRAPH_ID}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True, body

    started = _now()
    g = Graph()
    g.parse(data=body["ttl"], format="turtle")
    parse_ms = _elapsed_ms(started)

    _banner("large graph parsed")
    print(f"fetch_elapsed_ms={took_ms:.1f}")
    print(f"parse_elapsed_ms={parse_ms:.1f}")
    print(f"triple_count={len(g)}")
    return g


def _extract_large_graph_schema() -> dict[str, Any]:
    res, took_ms = _timed_request(
        "extract large graph schema",
        "POST",
        "/graphs/extract-schema",
        json={"graph_id": GRAPH_ID, "include_examples": False, "max_examples_per_edge": 3},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    _banner("large graph schema extracted")
    print(f"extract_elapsed_ms={took_ms:.1f}")
    print(f"class_edge_count={len(body.get('class_to_class_edges', []))}")
    return body


def _discover_target_class_for_property(g: Graph, property_uri: str) -> str:
    prop = URIRef(property_uri)
    candidates: dict[str, int] = {}

    for subj, _, _ in g.triples((None, prop, None)):
        for _, _, class_uri in g.triples((subj, RDF.type, None)):
            class_str = str(class_uri)
            candidates[class_str] = candidates.get(class_str, 0) + 1

    if not candidates:
        raise AssertionError(f"No typed subjects found in large graph for property {property_uri}")

    fbo_candidates = {k: v for k, v in candidates.items() if k.startswith(FBO)}
    selected_pool = fbo_candidates or candidates
    return max(selected_pool.items(), key=lambda kv: kv[1])[0]


def _property_exists(g: Graph, property_uri: str) -> bool:
    prop = URIRef(property_uri)
    return any(g.triples((None, prop, None)))


def _decision_node(node_id: str, target_class: str, spec: dict[str, Any], next_node_id: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "element_type": "decision",
        "target_class": target_class,
        "property_path": spec["property_uri"],
        "operator": spec["operator"],
        "value": spec["value"],
        "true_path": next_node_id,
        "false_path": next_node_id,
        "unknown_path": next_node_id,
        "label": spec["label"],
        "config": {},
    }


def _build_connectivity_maps(
    schema_payload: dict[str, Any],
) -> tuple[dict[tuple[str, str], list[str]], set[tuple[str, str]]]:
    forward_edges: dict[tuple[str, str], list[str]] = {}
    bidirectional_pairs: set[tuple[str, str]] = set()
    for edge in schema_payload.get("class_to_class_edges", []):
        from_class = edge["from_class"]
        to_class = edge["to_class"]
        predicates = sorted(edge.get("predicates", []))
        forward_edges[(from_class, to_class)] = predicates
        bidirectional_pairs.add((from_class, to_class))
        bidirectional_pairs.add((to_class, from_class))
    return forward_edges, bidirectional_pairs


def _best_connected_spec_chain(
    available_specs: list[dict[str, Any]],
    schema_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    forward_edges, bidirectional_pairs = _build_connectivity_maps(schema_payload)
    best_indices: list[int] = []

    def dfs(chain: list[int], remaining: set[int]) -> None:
        nonlocal best_indices
        if len(chain) > len(best_indices) or (len(chain) == len(best_indices) and chain < best_indices):
            best_indices = list(chain)
        if not chain:
            for idx in sorted(remaining):
                dfs([idx], remaining - {idx})
            return

        last_idx = chain[-1]
        last_class = available_specs[last_idx]["target_class"]
        candidates = []
        for idx in sorted(remaining):
            next_class = available_specs[idx]["target_class"]
            if (last_class, next_class) in bidirectional_pairs:
                candidates.append(idx)
        for idx in candidates:
            dfs(chain + [idx], remaining - {idx})

    dfs([], set(range(len(available_specs))))
    connected_specs = [available_specs[idx] for idx in best_indices]
    transition_notes: list[dict[str, Any]] = []
    for left, right in zip(best_indices, best_indices[1:], strict=False):
        from_class = available_specs[left]["target_class"]
        to_class = available_specs[right]["target_class"]
        if (from_class, to_class) in forward_edges:
            mode = "forward"
            predicates = forward_edges[(from_class, to_class)]
        else:
            mode = "reverse"
            predicates = forward_edges.get((to_class, from_class), [])
        transition_notes.append(
            {
                "from_label": available_specs[left]["label"],
                "to_label": available_specs[right]["label"],
                "from_class": from_class,
                "to_class": to_class,
                "mode": mode,
                "predicates": predicates,
            }
        )
    return connected_specs, transition_notes


def _run_options() -> dict[str, Any]:
    options = {
        "inference": "rdfs",
        "advanced": True,
        "include_report_text": True,
        "include_report_graph_ttl": False,
        "include_targets": True,
        "include_trace": True,
        "debug": True,
        "include_planner_debug": True,
        "fail_fast": False,
        "max_steps_per_target": 100,
    }
    if TEST_SOLVER:
        options["solver"] = TEST_SOLVER
    return options


def _expected_engine_name() -> str:
    solver = TEST_SOLVER or "standard"
    return EXPECTED_ENGINE_BY_SOLVER.get(solver, "standard_compiled_runtime")


def _assert_optional_report_text(body: dict[str, Any]) -> None:
    assert "report_text" in body
    assert body["report_text"] is None or isinstance(body["report_text"], str), body


def _print_check_response_stats(label: str, body: dict[str, Any], request_elapsed_ms: float) -> None:
    engine = body.get("engine") or {}
    summary = body.get("summary") or {}
    compatibility = body.get("compatibility") or {}
    debug = body.get("debug") or {}
    timings = body.get("timings", {}).get("rule_ms") or debug.get("timings_ms") or {}
    counters = debug.get("counters") or {}
    planner = body.get("planner") or debug.get("planner") or {}
    transition_inspections = body.get("transition_inspections") or debug.get("transition_inspections") or []
    terminal_failures = body.get("terminal_failures") or debug.get("terminal_failures") or []
    compiled_rule = body.get("compiled_rule") or {}
    targets = body.get("targets") or []
    trace = body.get("trace") or []

    _banner(label)
    print(f"request_elapsed_ms={request_elapsed_ms:.1f}")
    print(f"solver_requested={TEST_SOLVER or '<api default>'}")
    print(f"solver_used={engine.get('solver_mode')}")
    print(f"engine={engine.get('engine')}")
    print(f"runtime={engine.get('runtime')}")
    print(f"evaluator={engine.get('evaluator')}")
    print(f"target_resolver={engine.get('target_resolver')}")
    print(f"inference={engine.get('inference')}")
    print(f"advanced={engine.get('advanced')}")
    compiled_rule_keys = (
        sorted(compiled_rule.keys()) if isinstance(compiled_rule, dict) else type(compiled_rule).__name__
    )
    print(f"compiled_rule_keys={compiled_rule_keys}")
    print(
        "summary=",
        _pretty(
            {
                "total_targets": summary.get("total_targets"),
                "passed": summary.get("passed"),
                "failed": summary.get("failed"),
                "unknown": summary.get("unknown"),
                "conforms": summary.get("conforms"),
                "terminal_counts": summary.get("terminal_counts"),
                "missing_data_categories": summary.get("missing_data_categories"),
            }
        ),
    )
    print(
        "compatibility=",
        _pretty(
            {
                "compatibility_score": compatibility.get("compatibility_score"),
                "missing_target_classes": compatibility.get("missing_target_classes"),
                "missing_properties": compatibility.get("missing_properties"),
            }
        ),
    )
    print(f"targets_len={len(targets)}")
    print(f"trace_len={len(trace)}")
    if timings:
        print("timings_ms=", _pretty(timings))
    else:
        print("timings_ms=<none>")
    if counters:
        print("counters=", _pretty(counters))
    else:
        print("counters=<none>")
    if planner:
        print(
            "planner=",
            _pretty(
                {
                    "strategy": planner.get("strategy"),
                    "decision_order": planner.get("decision_order"),
                    "backward_safe": planner.get("backward_safe"),
                    "backward_terminal_nodes": planner.get("backward_terminal_nodes"),
                }
            ),
        )
    else:
        print("planner=<none>")
    if transition_inspections:
        print("transition_inspections_sample=", _pretty(transition_inspections[:5]))
    else:
        print("transition_inspections_sample=<none>")
    if terminal_failures:
        print("terminal_failures_sample=", _pretty(terminal_failures[:5]))
    else:
        print("terminal_failures_sample=<none>")


@pytest.fixture(scope="module")
def discovered_large_graph_data():
    graph = _parse_large_graph()
    schema_payload = _extract_large_graph_schema()

    available_specs = []
    missing_property_uris = []

    started = _now()
    for spec in CORE_PROPERTY_SPECS:
        if _property_exists(graph, spec["property_uri"]):
            target_class = _discover_target_class_for_property(graph, spec["property_uri"])
            available_specs.append({**spec, "target_class": target_class})
        else:
            missing_property_uris.append(spec["property_uri"])
    discovery_ms = _elapsed_ms(started)

    _banner("graph property discovery")
    print(f"discovery_elapsed_ms={discovery_ms:.1f}")
    print(f"available_specs_count={len(available_specs)}")
    print(f"missing_property_count={len(missing_property_uris)}")
    print("available_property_labels=", [spec["label"] for spec in available_specs])
    if missing_property_uris:
        print("missing_property_uris=", _pretty(missing_property_uris))

    return {
        "graph": graph,
        "schema_payload": schema_payload,
        "available_specs": available_specs,
        "missing_property_uris": missing_property_uris,
    }


@pytest.fixture(scope="module")
def complex_rule_bundle(discovered_large_graph_data):
    available_specs = discovered_large_graph_data["available_specs"]
    if not available_specs:
        pytest.skip("No expected properties were found in the large graph; cannot build combo integration rule.")
    connected_specs, transition_notes = _best_connected_spec_chain(
        available_specs,
        discovered_large_graph_data["schema_payload"],
    )
    if len(connected_specs) < 2:
        pytest.skip("Could not derive a connected property/class chain from the large graph schema.")

    rule_id = _uid("it-rule-complex-large-current-graph")
    pass_id = f"{rule_id}-pass"
    fail_id = f"{rule_id}-fail"
    unknown_id = f"{rule_id}-unknown"

    decision_nodes = []
    element_ids = []

    _banner("connected combo chain")
    print(f"available_specs_count={len(available_specs)}")
    print(f"connected_specs_count={len(connected_specs)}")
    print("connected_property_labels=", [spec["label"] for spec in connected_specs])
    print("connected_transitions=", _pretty(transition_notes))

    for idx, spec in enumerate(connected_specs, start=1):
        node_id = f"{rule_id}-d{idx:02d}"
        next_node_id = f"{rule_id}-d{idx + 1:02d}" if idx < len(connected_specs) else pass_id
        node = _decision_node(node_id, spec["target_class"], spec, next_node_id)
        decision_nodes.append(node)
        element_ids.append(node_id)

    end_nodes = [
        _end_node(pass_id, "assembled-pass", "passed"),
        _end_node(fail_id, "assembled-fail", "failed"),
        _end_node(unknown_id, "assembled-unknown", "unknown"),
    ]
    element_ids.extend([pass_id, fail_id, unknown_id])

    _banner("publish rule elements")
    print(f"rule_id={rule_id}")
    print(f"decision_node_count={len(decision_nodes)}")
    print(f"end_node_count={len(end_nodes)}")

    publish_started = _now()
    for index, node in enumerate(decision_nodes + end_nodes, start=1):
        node_started = _now()
        res = _request("POST", "/rules/rule-elements", json=node)
        node_elapsed_ms = _elapsed_ms(node_started)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["published"] is True, body
        print(
            f"[publish {index:02d}/{len(decision_nodes) + len(end_nodes):02d}] "
            f"id={node['id']} type={node['element_type']} elapsed_ms={node_elapsed_ms:.1f}"
        )
    total_publish_ms = _elapsed_ms(publish_started)
    print(f"total_rule_element_publish_elapsed_ms={total_publish_ms:.1f}")

    assembled_rule = {
        "id": rule_id,
        "entry": decision_nodes[0]["id"],
        "elements": decision_nodes + end_nodes,
    }

    bundle = {
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
    yield bundle
    if should_cleanup_generated_data():
        print("\nCleaning generated combo rule data ...")
        try:
            delete_rule(rule_id)
        except Exception as exc:
            print(f"Failed to delete generated combo rule '{rule_id}': {exc}")
        for element_id in element_ids:
            try:
                delete_rule_element(element_id)
            except Exception as exc:
                print(f"Failed to delete generated combo rule element '{element_id}': {exc}")


def test_large_graph_discovery_reports_which_current_properties_are_available(discovered_large_graph_data):
    available = discovered_large_graph_data["available_specs"]
    missing = discovered_large_graph_data["missing_property_uris"]

    assert len(available) >= 1
    assert len(available) + len(missing) == len(CORE_PROPERTY_SPECS)

    available_uris = {spec["property_uri"] for spec in available}
    assert f"{FBO}FireRating" in available_uris
    assert f"{FBO}HasBuiltInAlarm" in available_uris
    assert f"{FBO}LoadBearing" in available_uris
    assert f"{FBO}NumberOfOccupants" in available_uris
    assert f"{FBO}OccupancyType" in available_uris
    assert f"{FBO}HasSmokeDetectionCoverage" in available_uris
    assert f"{FBO}BuildingHeight" in available_uris
    assert f"{FBO}StoreyNumber" in available_uris
    assert f"{FBO}FireCompartmentId" in available_uris
    assert f"{FBO}FireExit" in available_uris


def test_complex_rule_elements_cover_available_large_graph_properties(complex_rule_bundle):
    decision_nodes = complex_rule_bundle["decision_nodes"]
    discovered_properties = {node["property_path"] for node in decision_nodes}
    connected_properties = {spec["property_uri"] for spec in complex_rule_bundle["connected_specs"]}

    assert len(decision_nodes) == len(connected_properties)
    assert discovered_properties == connected_properties
    assert len(decision_nodes) >= 2

    for node in decision_nodes:
        assert node["target_class"].startswith("http://")


def test_complex_rule_assembly_is_complete(complex_rule_bundle):
    payload = {
        "rule_id": complex_rule_bundle["rule_id"],
        "entry": complex_rule_bundle["entry"],
        "element_ids": complex_rule_bundle["element_ids"],
        "links": [],
        "validate": True,
    }

    res, took_ms = _timed_request("assemble rule", "POST", "/rules/assemble", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "assemble_response=",
        _pretty(
            {
                "elapsed_ms": round(took_ms, 1),
                "complete": body.get("complete"),
                "element_count": body.get("element_count"),
            }
        ),
    )

    assert body["complete"] is True, body
    assert body["entry_ok"] is True, body
    assert body["broken"] is False, body
    assert body["open"] is False, body
    assert body["element_count"] == len(complex_rule_bundle["element_ids"]), body


def test_complex_rule_check_completeness_is_complete_before_publish(complex_rule_bundle):
    res, took_ms = _timed_request(
        "check completeness before publish",
        "POST",
        "/rules/check-completeness",
        json={"rule": complex_rule_bundle["assembled_rule"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "completeness_before_publish=",
        _pretty({"elapsed_ms": round(took_ms, 1), "complete": body.get("complete"), "errors": body.get("errors")}),
    )

    assert body["complete"] is True, body
    assert body["entry_ok"] is True, body
    assert body["broken"] is False, body
    assert body["open"] is False, body
    assert body["missing_nodes"] == [], body
    assert body["open_branches"] == [], body
    assert body["errors"] == [], body


def test_complex_rule_assemble_compile_returns_ttl(complex_rule_bundle):
    payload = {
        "rule_id": complex_rule_bundle["rule_id"],
        "entry": complex_rule_bundle["entry"],
        "element_ids": complex_rule_bundle["element_ids"],
        "links": [],
        "validate": True,
    }

    res, took_ms = _timed_request("assemble compile rule", "POST", "/rules/assemble/compile", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "assemble_compile=",
        _pretty(
            {"elapsed_ms": round(took_ms, 1), "ttl_length": len(body.get("ttl", "")), "complete": body.get("complete")}
        ),
    )

    assert body["rule_id"] == complex_rule_bundle["rule_id"], body
    assert body["ttl"], body
    assert "sh:" in body["ttl"], body
    assert body["complete"] is True, body
    assert body["broken"] is False, body


def test_complex_rule_publish_to_fuseki_via_api(complex_rule_bundle):
    res, took_ms = _timed_request(
        "publish assembled rule",
        "POST",
        "/rules/publish",
        json={"rule": complex_rule_bundle["assembled_rule"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "publish_rule=",
        _pretty(
            {"elapsed_ms": round(took_ms, 1), "published": body.get("published"), "graph_uri": body.get("graph_uri")}
        ),
    )

    assert body["published"] is True, body
    assert body["rule_id"] == complex_rule_bundle["rule_id"], body
    assert body["graph_uri"].endswith(complex_rule_bundle["rule_id"]), body


def test_complex_rule_check_completeness_is_complete_after_publish(complex_rule_bundle):
    res, took_ms = _timed_request(
        "check completeness after publish",
        "POST",
        "/rules/check-completeness",
        json={"rule": complex_rule_bundle["assembled_rule"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "completeness_after_publish=",
        _pretty({"elapsed_ms": round(took_ms, 1), "complete": body.get("complete"), "errors": body.get("errors")}),
    )

    assert body["complete"] is True, body
    assert body["entry_ok"] is True, body
    assert body["broken"] is False, body
    assert body["open"] is False, body
    assert body["missing_nodes"] == [], body
    assert body["open_branches"] == [], body
    assert body["errors"] == [], body


def test_complex_rule_fetch_after_publish(complex_rule_bundle):
    rule_id = complex_rule_bundle["rule_id"]

    res, took_ms = _timed_request("fetch published rule", "GET", f"/rules/{rule_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "fetch_rule=",
        _pretty(
            {
                "elapsed_ms": round(took_ms, 1),
                "rule_id": body.get("rule", {}).get("id"),
                "element_count": len(body.get("rule", {}).get("elements", [])),
            }
        ),
    )

    assert body["graph_uri"].endswith(rule_id), body
    assert body["ttl"], body
    assert body["rule"] is not None, body
    assert body["rule"]["id"] == rule_id, body
    assert body["rule"]["entry"] == complex_rule_bundle["entry"], body
    assert len(body["rule"]["elements"]) == len(complex_rule_bundle["assembled_rule"]["elements"]), body


def test_complex_rule_list_contains_published_rule(complex_rule_bundle):
    rule_id = complex_rule_bundle["rule_id"]

    res, took_ms = _timed_request("list rules", "GET", "/rules/list")
    assert res.status_code == 200, res.text
    body = res.json()

    ids = {item["rule_id"] for item in body.get("rules", [])}
    print(
        "list_rules=",
        _pretty({"elapsed_ms": round(took_ms, 1), "rule_count": len(ids), "contains_current_rule": rule_id in ids}),
    )
    assert rule_id in ids, body


def test_complex_rule_compatibility_against_large_graph_is_full_for_available_properties(complex_rule_bundle):
    res, took_ms = _timed_request(
        "compatibility check",
        "POST",
        "/checks/compatibility",
        json={"rule_id": complex_rule_bundle["rule_id"], "graph_id": GRAPH_ID},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        "compatibility_response=",
        _pretty(
            {
                "elapsed_ms": round(took_ms, 1),
                "compatibility_score": body.get("compatibility_score"),
                "missing_target_classes": body.get("missing_target_classes"),
                "missing_properties": body.get("missing_properties"),
            }
        ),
    )

    assert body["rule_id"] == complex_rule_bundle["rule_id"], body
    assert body["graph_id"] == GRAPH_ID, body
    assert body["compatibility_score"] == pytest.approx(1.0), body
    assert body["missing_target_classes"] == [], body
    assert body["missing_properties"] == [], body


# @pytest.mark.slow
def test_complex_rule_run_against_large_graph_returns_execution_contract(complex_rule_bundle):
    options = _run_options()
    _banner("checks run configuration")
    print(f"base_url={BASE_URL}")
    print(f"graph_id={GRAPH_ID}")
    print(f"rule_id={complex_rule_bundle['rule_id']}")
    print(f"solver_requested={TEST_SOLVER or '<api default>'}")
    print("options=", _pretty(options))

    res, took_ms = _timed_request(
        "run checks on large graph",
        "POST",
        "/checks/run-diagnostic",
        json={
            "rule_id": complex_rule_bundle["rule_id"],
            "graph_id": GRAPH_ID,
            "options": options,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()

    _print_check_response_stats("checks run response stats", body, took_ms)

    assert body["success"] is True, body
    assert body["rule_id"] == complex_rule_bundle["rule_id"], body
    assert body["graph_id"] == GRAPH_ID, body
    assert body["solver"] in {"standard", "horizontal", "vertical"}, body

    assert body["engine"]["engine"] == _expected_engine_name(), body
    assert body["engine"]["inference"] == "rdfs", body
    assert body["engine"]["advanced"] is True, body
    assert body["engine"]["diagnostics_enabled"] is True, body

    assert "summary" in body, body
    assert "compatibility" in body, body
    assert "compiled_rule" in body, body
    assert "targets" in body, body
    assert "trace" in body, body
    assert "report_text" in body, body
    assert "end_nodes" in body, body
    assert "timings" in body, body

    assert isinstance(body["targets"], list), body
    assert isinstance(body["trace"], list), body
    _assert_optional_report_text(body)

    summary = body["summary"]
    assert summary["total_targets"] >= 1, body
    assert summary["passed"] + summary["failed"] + summary["unknown"] == summary["total_targets"], body

    compatibility = body["compatibility"]
    assert compatibility["compatibility_score"] == pytest.approx(1.0), body
    assert compatibility["missing_target_classes"] == [], body
    assert compatibility["missing_properties"] == [], body
