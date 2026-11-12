"""Rule elements v2: sequence property paths, $ref values and derive elements.

Covers: expression language, element validation + rule lint, compile -> decompile round trip, and execution in all
four check algorithms (standard, horizontal, vertical, blind) plus the in-memory RuleEngine.
"""

from __future__ import annotations

import pytest

from app.checks.blind_runner import BlindShaclRuleRunner
from app.checks.compiler import RuleCompiler
from app.checks.evaluator_hybrid import HybridAtomicEvaluator
from app.checks.result_models import GraphRef, RunOptions
from app.checks.runtime import CheckRuntime
from app.checks.runtime_horizontal_layered import HorizontalLayeredCheckRuntime
from app.checks.runtime_vertical_pathway import VerticalPathwayCheckRuntime
from app.rules.decompiler import decompile_ruleset_ttl
from app.rules.element_validator import validate_rule_element_payload
from app.rules.engine import RuleEngine
from app.rules.expressions import ExpressionError, ExpressionUnknown, evaluate, parse_expression, referenced_paths
from app.rules.lint import lint_rule
from app.rules.models import fbRule, fbRuleElement
from app.rules.paths import expand_curie, split_property_path
from app.rules.ruleset import Rule
from app.rules.shacl_builder import SHACLBuilder

FBO = "https://firebim.org/ontologies/fbo#"
DK = "https://firebim.org/ontologies/fbo/dk#"
LT = "https://firebim.org/ontologies/fbo/lt#"

PAIR_GRAPH = """
@prefix fbo:    <https://firebim.org/ontologies/fbo#> .
@prefix fbo-dk: <https://firebim.org/ontologies/fbo/dk#> .
@prefix :       <https://example.org/building/42#> .

:fc1 a fbo:FireCompartment ; fbo-dk:UseCategory "AK2" ; fbo-dk:RiskClass "RK2" .
:fc2 a fbo:FireCompartment ; fbo-dk:UseCategory "AK2" ; fbo-dk:RiskClass "RK2" .
:fc3 a fbo:FireCompartment ; fbo-dk:UseCategory "AK4" ; fbo-dk:RiskClass "RK2" .
:fc4 a fbo:FireCompartment ; fbo-dk:UseCategory "AK2" .

:wall7 a fbo:WallSeparating ; fbo:FireResistance 60 .
:deck3 a fbo:DeckSeparating ; fbo:FireResistance 30 .

:adj_1 a fbo:Adjacency ; fbo:from :fc1 ; fbo:to :fc2 ; fbo:via :wall7 .
:adj_2 a fbo:Adjacency ; fbo:from :fc2 ; fbo:to :fc3 ; fbo:via :deck3 .
:adj_3 a fbo:Adjacency ; fbo:from :fc3 ; fbo:to :fc4 .
"""

LT_GRAPH = """
@prefix fbo-lt: <https://firebim.org/ontologies/fbo/lt#> .
@prefix :       <https://example.org/building/7#> .

:c1 a fbo-lt:FireCompartment ; fbo-lt:FsAreaConditionalFireCompartment 4000 ; fbo-lt:GCoeficient 0.71 ;
    fbo-lt:FireCompartmentHeight 12 ; fbo-lt:HabsCalculatedAltitude 28 ; fbo-lt:BuildingStoreyFloorArea 2000 .
:c2 a fbo-lt:FireCompartment ; fbo-lt:FsAreaConditionalFireCompartment 4000 ; fbo-lt:GCoeficient 1 ;
    fbo-lt:FireCompartmentHeight 12 ; fbo-lt:HabsCalculatedAltitude 28 ; fbo-lt:BuildingStoreyFloorArea 5000 ;
    fbo-lt:MaxAreaFireCompartment 1 .
:c3 a fbo-lt:FireCompartment ; fbo-lt:FsAreaConditionalFireCompartment 4000 ; fbo-lt:GCoeficient 1 ;
    fbo-lt:BuildingStoreyFloorArea 100 .
"""


def _end(rule_id: str, suffix: str, kind: str) -> dict:
    return {
        "id": f"{rule_id}-{suffix}",
        "element_type": "end",
        "label": suffix,
        "config": {"output_text": suffix, "end_kind": kind},
    }


def pair_rule() -> dict:
    """target = fbo:Adjacency; same UseCategory ($ref) -> wall good enough (sequence path) -> pass."""
    rid = "t-pair"
    return {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-same-use", "label": "start"},
            {
                "id": f"{rid}-same-use",
                "element_type": "decision",
                "target_class": f"{FBO}Adjacency",
                "property_path": f"{FBO}from/{DK}UseCategory",
                "operator": "==",
                "value": {"$ref": f"{FBO}to/{DK}UseCategory"},
                "true_path": f"{rid}-wall",
                "false_path": f"{rid}-separate",
                "unknown_path": f"{rid}-ask",
                "label": "same use category?",
            },
            {
                "id": f"{rid}-wall",
                "element_type": "decision",
                "target_class": f"{FBO}Adjacency",
                "property_path": f"{FBO}via/{FBO}FireResistance",
                "operator": ">=",
                "value": 60,
                "true_path": f"{rid}-share",
                "false_path": f"{rid}-separate",
                "unknown_path": f"{rid}-ask",
                "label": "separating element good enough?",
            },
            _end(rid, "share", "pass"),
            _end(rid, "separate", "fail"),
            _end(rid, "ask", "missing_data"),
        ],
    }


def lt_rule() -> dict:
    """decision (G == 1) -> derive Fg (two branches) -> decision storey area < Fg (reads the derived value)."""
    rid = "t-fg"
    fs, g, h, habs = (
        f"${LT}FsAreaConditionalFireCompartment",
        f"${LT}GCoeficient",
        f"${LT}FireCompartmentHeight",
        f"${LT}HabsCalculatedAltitude",
    )
    return {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-g1", "label": "start"},
            {
                "id": f"{rid}-g1",
                "element_type": "decision",
                "target_class": f"{LT}FireCompartment",
                "property_path": f"{LT}GCoeficient",
                "operator": "==",
                "value": 1,
                "true_path": f"{rid}-fg-opt1",
                "false_path": f"{rid}-fg-opt2",
                "unknown_path": f"{rid}-ask",
                "label": "G = 1?",
            },
            {
                "id": f"{rid}-fg-opt1",
                "element_type": "derive",
                "target_class": f"{LT}FireCompartment",
                "property_path": f"{LT}MaxAreaFireCompartment",
                "expression": f"{fs} * cosd(90 * {h} / {habs})",
                "true_path": f"{rid}-area",
                "unknown_path": f"{rid}-ask",
                "label": "Fg option 1",
            },
            {
                "id": f"{rid}-fg-opt2",
                "element_type": "derive",
                "target_class": f"{LT}FireCompartment",
                "property_path": f"{LT}MaxAreaFireCompartment",
                "expression": f"{fs} * {g} * cosd(90 * {h} / {habs})",
                "true_path": f"{rid}-area",
                "unknown_path": f"{rid}-ask",
                "label": "Fg option 2",
            },
            {
                "id": f"{rid}-area",
                "element_type": "decision",
                "target_class": f"{LT}FireCompartment",
                "property_path": f"{LT}BuildingStoreyFloorArea",
                "operator": "<",
                "value": {"$ref": f"{LT}MaxAreaFireCompartment"},
                "true_path": f"{rid}-ok",
                "false_path": f"{rid}-split",
                "unknown_path": f"{rid}-ask",
                "label": "storey area < Fg?",
            },
            _end(rid, "ok", "pass"),
            _end(rid, "split", "fail"),
            _end(rid, "ask", "missing_data"),
        ],
    }


def _build(rule: dict) -> Rule:
    built = Rule(rule["id"])
    for el in rule["elements"]:
        node = {**el, "node_type": el["element_type"]}
        validate_rule_element_payload(node)
        built.add(
            fbRuleElement(
                id=el["id"],
                target_class=el.get("target_class"),
                property_path=el.get("property_path"),
                operator=el.get("operator"),
                value=el.get("value"),
                expression=el.get("expression"),
                true_path=el.get("true_path"),
                false_path=el.get("false_path"),
                unknown_path=el.get("unknown_path"),
                label=el.get("label"),
                node_type=el["element_type"],
                config=el.get("config") or {},
            )
        )
    built.start(rule["entry"])
    built.validate()
    return built


def _ttl(rule: dict) -> str:
    builder = SHACLBuilder()
    builder.add_rule_set(_build(rule))
    return builder.get_graph().serialize(format="turtle")


# ----------------------------------------------------------------------------------------------- paths + expressions
def test_split_property_path_handles_full_iris_and_curies():
    assert split_property_path(f"{FBO}from/{DK}UseCategory") == [f"{FBO}from", f"{DK}UseCategory"]
    assert split_property_path("fbo:from/fbo-dk:UseCategory") == ["fbo:from", "fbo-dk:UseCategory"]
    assert split_property_path(f"{FBO}UseCategory") == [f"{FBO}UseCategory"]
    assert expand_curie("fbo-lt:GCoeficient") == f"{LT}GCoeficient"


def test_expression_language():
    ast = parse_expression("$fbo-lt:Fs * $fbo-lt:G * cosd(90 * 12 / 28) + lookup(7, [[0, 6, 25], [6, 12, 20]])")
    assert referenced_paths(ast) == ["fbo-lt:Fs", "fbo-lt:G"]
    values = {"fbo-lt:Fs": 4000, "fbo-lt:G": 0.71}
    result = evaluate(ast, lambda p: values[p])
    assert result == pytest.approx(4000 * 0.71 * 0.7818314824680298 + 20)
    assert evaluate(parse_expression('"AK3"'), lambda p: None) == "AK3"
    with pytest.raises(ExpressionError):
        parse_expression("1 + (2 * 3")
    with pytest.raises(ExpressionError):
        parse_expression("if(1, 2, 3)")
    with pytest.raises(ExpressionUnknown):
        evaluate(parse_expression("1 / $x"), lambda p: 0)
    with pytest.raises(ExpressionUnknown):
        evaluate(parse_expression("lookup(99, [[0, 6, 25]])"), lambda p: None)


# ----------------------------------------------------------------------------------------------- validation + lint
def test_validator_rules_for_derive_and_ref():
    ok = {
        "id": "d",
        "node_type": "derive",
        "target_class": "x",
        "property_path": f"{LT}Fg",
        "expression": "$fbo-lt:GCoeficient * 2",
        "true_path": "n",
    }
    validate_rule_element_payload(ok)
    with pytest.raises(ValueError, match="reads the property it writes"):
        validate_rule_element_payload({**ok, "expression": "$fbo-lt:Fg * 2"})
    with pytest.raises(ValueError, match="must not define"):
        validate_rule_element_payload({**ok, "operator": "=="})
    with pytest.raises(ValueError, match="expression is invalid"):
        validate_rule_element_payload({**ok, "expression": "2 *"})
    with pytest.raises(ValueError, match="between"):
        validate_rule_element_payload(
            {
                "id": "q",
                "node_type": "decision",
                "target_class": "x",
                "property_path": "p",
                "operator": "between",
                "value": {"$ref": "q"},
            }
        )
    with pytest.raises(ValueError, match="node_type must be one of"):
        validate_rule_element_payload({"id": "z", "node_type": "compute"})


def test_lint_detects_cycles_self_compare_and_read_before_write():
    rule = lt_rule()
    errors, warnings = lint_rule(rule)
    assert errors == []
    assert any("written by several derive elements" in w for w in warnings)
    # read-before-write: area decision reads MaxAreaFireCompartment, both derives precede it on every path -> no warning
    assert not any("read-before-write" in w for w in warnings)

    looped = lt_rule()
    looped["elements"][-3]["true_path"] = "t-fg-g1"  # ok end replaced by a jump back
    looped["elements"][-3]["element_type"] = "decision"
    looped["elements"][-3].update(
        {
            "target_class": "x",
            "property_path": "p",
            "operator": "==",
            "value": 1,
            "false_path": "t-fg-split",
            "unknown_path": "t-fg-ask",
        }
    )
    errors, _ = lint_rule(looped)
    assert any(e.startswith("cycle:") for e in errors)

    same = pair_rule()
    same["elements"][1]["value"] = {"$ref": same["elements"][1]["property_path"]}
    _, warnings = lint_rule(same)
    assert any("always True" in w for w in warnings)


# ----------------------------------------------------------------------------------------------- compile round trip
def test_compile_decompile_round_trip_keeps_paths_refs_and_derive():
    for rule in (pair_rule(), lt_rule()):
        ttl = _ttl(rule)
        back = decompile_ruleset_ttl(ttl)
        by_id = {n["id"]: n for n in back["nodes"]}
        for el in rule["elements"]:
            node = by_id[el["id"]]
            assert node["node_type"] == el["element_type"]
            if el["element_type"] == "decision":
                assert node["property_path"] == el["property_path"]
                assert node["operator"] == el["operator"]
                assert node["value"] == el["value"]
            if el["element_type"] == "derive":
                assert node["property_path"] == el["property_path"]
                assert node["expression"] == el["expression"]
                assert node["target_class"] == el["target_class"]
                assert node["true_path"] == el["true_path"].split("-", 2)[-1] or node["true_path"] == el["true_path"]

    compiled = RuleCompiler().compile_from_ttl(_ttl(lt_rule()))
    assert compiled.metadata["derive_count"] == 2
    assert compiled.nodes["t-fg-fg-opt1"].node_type == "derive"
    assert compiled.nodes["t-fg-area"].value == {"$ref": f"{LT}MaxAreaFireCompartment"}


# ----------------------------------------------------------------------------------------------- execution
def _run(runtime_cls, rule: dict, ttl: str, graph_id: str = "g"):
    compiled = RuleCompiler().compile_from_ttl(_ttl(rule))
    runtime = runtime_cls(evaluator=HybridAtomicEvaluator())
    graph_ref = GraphRef(graph_id=graph_id, graph_uri=f"https://firebim.org/graphs/buildings/{graph_id}", ttl=ttl)
    return runtime.run_rule(graph_ref=graph_ref, compiled=compiled, options=RunOptions(diagnostics=True))


def _status_by_target(result) -> dict[str, str]:
    return {t.target_node.split("#")[-1]: t.final_status for t in result.targets}


@pytest.mark.parametrize("runtime_cls", [CheckRuntime, HorizontalLayeredCheckRuntime, VerticalPathwayCheckRuntime])
def test_pair_rule_sequence_paths_and_ref(runtime_cls):
    result = _run(runtime_cls, pair_rule(), PAIR_GRAPH)
    statuses = _status_by_target(result)
    assert statuses == {"adj_1": "True", "adj_2": "False", "adj_3": "False"}
    adj1 = next(t for t in result.targets if t.target_node.endswith("adj_1"))
    step = adj1.trace[0]
    assert step.evidence["reference_path"] == f"{FBO}to/{DK}UseCategory"
    assert step.evidence["actual_value"] == "AK2" and step.evidence["expected_value"] == "AK2"


@pytest.mark.parametrize("runtime_cls", [CheckRuntime, HorizontalLayeredCheckRuntime, VerticalPathwayCheckRuntime])
def test_lt_rule_derive_then_compare(runtime_cls):
    result = _run(runtime_cls, lt_rule(), LT_GRAPH)
    statuses = _status_by_target(result)
    # c1: G=0.71 -> option 2: 4000*0.71*cosd(38.57)=2220 > 2000 -> True
    # c2: G=1 -> option 1: 4000*cosd(38.57)=3127; base graph says MaxArea=1 but the derived value shadows it;
    #     5000 > 3127 -> False
    # c3: missing height -> derive Unknown -> ask
    assert statuses == {"c1": "True", "c2": "False", "c3": "Unknown"}
    assert {r["target"].split("#")[-1]: round(r["value"]) for r in result.derived_values} == {"c1": 2220, "c2": 3127}
    c2 = next(r for r in result.derived_values if r["target"].endswith("c2"))
    assert c2["node_id"] == "t-fg-fg-opt1"
    c2_trace = next(t for t in result.targets if t.target_node.endswith("c2"))
    derive_step = c2_trace.trace[1]
    assert derive_step.node_type == "derive" and derive_step.status == "True"
    assert derive_step.evidence["shadowed_base_values"] == [1]
    area_step = c2_trace.trace[2]
    assert area_step.evidence["expected_value"] == pytest.approx(3127.3, abs=0.5)
    assert result.derived_graph_uri == "https://firebim.org/graphs/derived/g/t-fg"
    assert result.derived_graph_ttl and "MaxAreaFireCompartment" in result.derived_graph_ttl
    c3 = next(t for t in result.targets if t.target_node.endswith("c3"))
    assert c3.terminal_node_id == "t-fg-ask"
    assert c3.trace[1].evidence["reason_code"] == "missing_required_input"


def test_blind_runner_handles_derive_and_lazy_decisions():
    runner = BlindShaclRuleRunner()
    result = runner.run_rule(
        graph_id="g",
        graph_uri="https://firebim.org/graphs/buildings/g",
        graph_ttl=LT_GRAPH,
        rule_id="t-fg",
        rule_ttl=_ttl(lt_rule()),
        include_debug=True,
    )
    by_target = {hit["target_node"].split("#")[-1]: hit["final_status"] for hit in result["end_outputs"]}
    assert by_target == {"c1": "True", "c2": "False", "c3": "Unknown"}
    assert result["debug"]["lazy_decision_nodes"] == ["t-fg-area"]
    assert len(result["derived_values"]) == 2

    pair = runner.run_rule(
        graph_id="g",
        graph_uri="https://firebim.org/graphs/buildings/g",
        graph_ttl=PAIR_GRAPH,
        rule_id="t-pair",
        rule_ttl=_ttl(pair_rule()),
    )
    by_target = {hit["target_node"].split("#")[-1]: hit["final_status"] for hit in pair["end_outputs"]}
    assert by_target == {"adj_1": "True", "adj_2": "False", "adj_3": "False"}


def test_in_memory_rule_engine_supports_derive():
    rule = fbRule(id="mem", entry_element="g1")
    rule.add_element(
        fbRuleElement(
            id="g1",
            target_class="c",
            property_path=f"{LT}GCoeficient",
            operator="==",
            value=1,
            true_path="d",
            false_path="d",
        )
    )
    rule.add_element(
        fbRuleElement(
            id="d",
            node_type="derive",
            target_class="c",
            property_path=f"{LT}Fg",
            expression=f"${LT}FsAreaConditionalFireCompartment * 2",
            true_path="end",
        )
    )
    rule.add_element(fbRuleElement(id="end", node_type="end", config={"terminal_value": "done"}))
    data = {f"{LT}FsAreaConditionalFireCompartment": 21}
    engine = RuleEngine(rule, lambda element, graph: "True")
    assert engine.execute(data_graph=data) == "done"
    assert engine.derived[f"{LT}Fg"] == 42


# ----------------------------------------------------------------------------------------------- API surface
@pytest.fixture
def api_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import checks as checks_api
    from app.api import rules as rules_api

    rule_ttls = {"t-fg": _ttl(lt_rule()), "t-pair": _ttl(pair_rule())}
    published: dict[str, str] = {}

    def fetch_rule(rule_id: str):
        return f"https://firebim.org/graphs/rulebook/{rule_id}", rule_ttls[rule_id]

    def fetch_building(graph_uri: str):
        if graph_uri.endswith("/buildings/lt"):
            return LT_GRAPH
        if graph_uri.endswith("/buildings/pair"):
            return PAIR_GRAPH
        if graph_uri in published:
            return published[graph_uri]
        raise KeyError(graph_uri)

    monkeypatch.setattr(checks_api, "fetch_rule_ttl", fetch_rule)
    monkeypatch.setattr(checks_api, "fetch_building_graph_ttl", fetch_building)
    monkeypatch.setattr(
        checks_api, "publish_building_graph_ttl", lambda ttl_text, graph_uri: published.__setitem__(graph_uri, ttl_text)
    )
    from app.auth.jwt import get_current_active_user

    app = FastAPI()
    app.include_router(rules_api.router)
    app.include_router(checks_api.router)
    app.dependency_overrides[get_current_active_user] = lambda: object()
    client = TestClient(app)
    client.published = published  # type: ignore[attr-defined]
    return client


def test_validate_endpoint_returns_warnings_and_blocks_cycles(api_client):
    res = api_client.post("/rules/validate", json={"rule": lt_rule()})
    assert res.status_code == 200 and res.json()["valid"] is True
    assert any("several derive elements" in w for w in res.json()["warnings"])

    looped = lt_rule()
    looped["elements"][2]["true_path"] = "t-fg-g1"  # derive -> back to the first decision
    res = api_client.post("/rules/validate", json={"rule": looped})
    assert res.json()["valid"] is False and "cycle" in res.json()["errors"][0]
    res = api_client.post("/rules/compile", json={"rule": looped})
    assert res.status_code == 400

    res = api_client.post("/rules/compile", json={"rule": pair_rule()})
    assert res.status_code == 200
    back = api_client.post("/rules/decompile", json={"ttl": res.json()["ttl"]}).json()["rule"]
    same_use = next(e for e in back["elements"] if e["id"] == "t-pair-same-use")
    assert same_use["value"] == {"$ref": f"{FBO}to/{DK}UseCategory"}
    assert same_use["property_path"] == f"{FBO}from/{DK}UseCategory"


@pytest.mark.parametrize("solver", ["standard", "horizontal", "vertical"])
def test_check_endpoints_return_derived_values_and_persist(api_client, solver):
    res = api_client.post(
        "/checks/run-diagnostic",
        json={"rule_id": "t-fg", "graph_id": "lt", "options": {"solver": solver, "persist_derived": True}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"] == {
        "total_targets": 3,
        "passed": 1,
        "failed": 1,
        "unknown": 1,
        "conforms": False,
        "terminal_counts": {"t-fg-ok": 1, "t-fg-split": 1, "t-fg-ask": 1},
        "missing_data_categories": body["summary"]["missing_data_categories"],
    }
    assert {d["target"].split("#")[-1] for d in body["derived_values"]} == {"c1", "c2"}
    assert body["derived_graph_uri"] == "https://firebim.org/graphs/derived/lt/t-fg"
    assert body["derived_persisted"] is True
    assert body["derived_graph_uri"] in api_client.published
    derive_steps = [s for t in body["targets"] for s in t["trace"] if s["node_type"] == "derive"]
    assert derive_steps and all("expression" in s["evidence"] for s in derive_steps)

    # a later rule can read the persisted values through use_derived_from
    merged_res = api_client.post(
        "/checks/run",
        json={"rule_id": "t-pair", "graph_id": "pair", "options": {"solver": solver, "use_derived_from": ["t-fg"]}},
    )
    assert merged_res.status_code == 200


def test_blind_endpoint_reports_derived_values(api_client):
    res = api_client.post(
        "/checks/run-blind", json={"rule_id": "t-fg", "graph_id": "lt", "options": {"include_debug": True}}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["passed"] == 1 and body["summary"]["failed"] == 1 and body["summary"]["unknown"] == 1
    assert len(body["derived_values"]) == 2


# ------------------------------------------------------------------------------ canvas import + end kinds
def test_workspace_from_published_rule_keeps_derive_expression():
    from app.api.rulebuilder import _normalize_rule_draft
    from app.rulebuilder.layout import build_workspace_from_rule

    rule = lt_rule()
    draft = _normalize_rule_draft(decompile_ruleset_ttl(_ttl(rule)))
    workspace = build_workspace_from_rule(draft)
    by_id = {node.canvas_node_id: node.rule for node in workspace.nodes}
    for el in rule["elements"]:
        assert el["id"] in by_id
        if el["element_type"] == "derive":
            assert by_id[el["id"]].element_type == "derive"
            assert by_id[el["id"]].expression == el["expression"]
        if el["element_type"] == "decision":
            assert by_id[el["id"]].value == el["value"]
    assert len(workspace.edges) == sum(
        1 for el in rule["elements"] for b in ("true_path", "false_path", "unknown_path") if el.get(b)
    )


def test_end_kind_vocabulary_is_shared_by_all_runners():
    from app.checks.end_status import status_from_end_kind

    assert status_from_end_kind("pass") == "True"
    assert status_from_end_kind("fail") == "False"
    assert status_from_end_kind("missing_data") == "Unknown"
    assert status_from_end_kind("not_applicable") == "True"
    assert status_from_end_kind("terminal") == "True"
    assert status_from_end_kind("output") == "True"
    assert status_from_end_kind("something-else") is None

    rid = "t-kinds"
    rule = {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-q", "label": "start"},
            {
                "id": f"{rid}-q",
                "element_type": "decision",
                "target_class": f"{LT}Compartment",
                "property_path": f"{LT}Area",
                "operator": ">",
                "value": 100,
                "true_path": f"{rid}-big",
                "false_path": f"{rid}-na",
                "unknown_path": f"{rid}-ask",
                "label": "big?",
            },
            _end(rid, "big", "terminal"),
            _end(rid, "na", "not_applicable"),
            _end(rid, "ask", "missing_data"),
        ],
    }
    ttl = f"""
    @prefix lt: <{LT}> . @prefix ex: <https://example.org/x#> .
    ex:c1 a lt:Compartment ; lt:Area 150 . ex:c2 a lt:Compartment ; lt:Area 50 . ex:c3 a lt:Compartment .
    """
    for runtime_cls in (CheckRuntime, HorizontalLayeredCheckRuntime, VerticalPathwayCheckRuntime):
        assert _status_by_target(_run(runtime_cls, rule, ttl)) == {"c1": "True", "c2": "True", "c3": "Unknown"}
    blind = BlindShaclRuleRunner().run_rule(
        graph_id="g",
        graph_uri="https://firebim.org/graphs/buildings/g",
        graph_ttl=ttl,
        rule_id=rid,
        rule_ttl=_ttl(rule),
    )
    assert {h["target_node"].split("#")[-1]: h["final_status"] for h in blind["end_outputs"]} == {
        "c1": "True",
        "c2": "True",
        "c3": "Unknown",
    }


def test_sequence_path_collapses_identical_values():
    from rdflib import Graph

    from app.checks.derived import resolve_path_values

    g = Graph()
    g.parse(
        data=f"""
        @prefix lt: <{LT}> . @prefix ex: <https://example.org/x#> .
        ex:u lt:has ex:c1, ex:c2, ex:c3 .
        ex:c1 lt:Cat "AK2" . ex:c2 lt:Cat "AK2" . ex:c3 lt:Cat "AK3" .
        ex:v lt:has ex:c1, ex:c2 .
        """,
        format="turtle",
    )
    assert [str(v) for v in resolve_path_values(g, "https://example.org/x#v", [f"{LT}has", f"{LT}Cat"])] == ["AK2"]
    assert sorted(str(v) for v in resolve_path_values(g, "https://example.org/x#u", [f"{LT}has", f"{LT}Cat"])) == [
        "AK2",
        "AK3",
    ]


def test_inverse_path_segments_round_trip_and_execute():
    from app.checks.evaluator_pyshacl import PyShaclAtomicEvaluator

    assert split_property_path(f"^{LT}has/{LT}Flag") == [f"^{LT}has", f"{LT}Flag"]
    assert expand_curie("^fbo-lt:has") == f"^{LT}has"

    rid = "t-inv"
    rule = {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-q", "label": "start"},
            {
                "id": f"{rid}-q",
                "element_type": "decision",
                "target_class": f"{LT}Compartment",
                "property_path": f"^{LT}has/{LT}Flag",  # compartment -> the building that lists it -> Flag
                "operator": "==",
                "value": True,
                "true_path": f"{rid}-ok",
                "false_path": f"{rid}-no",
                "unknown_path": f"{rid}-ask",
                "label": "building flag?",
            },
            _end(rid, "ok", "pass"),
            _end(rid, "no", "fail"),
            _end(rid, "ask", "missing_data"),
        ],
    }
    ttl = f"""
    @prefix lt: <{LT}> . @prefix ex: <https://example.org/x#> .
    ex:b1 lt:has ex:c1, ex:c2 ; lt:Flag true .
    ex:b2 lt:has ex:c3 ; lt:Flag false .
    ex:c1 a lt:Compartment . ex:c2 a lt:Compartment . ex:c3 a lt:Compartment . ex:c4 a lt:Compartment .
    """
    rule_ttl = _ttl(rule)
    back = decompile_ruleset_ttl(rule_ttl)
    assert {n["id"]: n for n in back["nodes"]}[f"{rid}-q"]["property_path"] == f"^{LT}has/{LT}Flag"
    expected = {"c1": "True", "c2": "True", "c3": "False", "c4": "Unknown"}
    for runtime_cls in (CheckRuntime, HorizontalLayeredCheckRuntime, VerticalPathwayCheckRuntime):
        assert _status_by_target(_run(runtime_cls, rule, ttl)) == expected
    # pySHACL evaluator builds sh:inversePath inside the sequence list
    compiled = RuleCompiler().compile_from_ttl(rule_ttl)
    runtime = CheckRuntime(evaluator=PyShaclAtomicEvaluator())
    graph_ref = GraphRef(graph_id="g", graph_uri="https://firebim.org/graphs/buildings/g", ttl=ttl)
    result = runtime.run_rule(graph_ref=graph_ref, compiled=compiled, options=RunOptions(diagnostics=True))
    assert _status_by_target(result) == expected
    blind = BlindShaclRuleRunner().run_rule(
        graph_id="g", graph_uri="https://firebim.org/graphs/buildings/g", graph_ttl=ttl, rule_id=rid, rule_ttl=rule_ttl
    )
    assert {h["target_node"].split("#")[-1]: h["final_status"] for h in blind["end_outputs"]} == expected


def test_blind_runner_treats_conflicting_multi_value_results_as_unknown():
    rid = "t-multi"
    rule = {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-q", "label": "start"},
            {
                "id": f"{rid}-q",
                "element_type": "decision",
                "target_class": f"{LT}Compartment",
                "property_path": f"{LT}Flag",
                "operator": "==",
                "value": True,
                "true_path": f"{rid}-ok",
                "false_path": f"{rid}-no",
                "unknown_path": f"{rid}-ask",
                "label": "flag?",
            },
            _end(rid, "ok", "pass"),
            _end(rid, "no", "fail"),
            _end(rid, "ask", "missing_data"),
        ],
    }
    ttl = f"""
    @prefix lt: <{LT}> . @prefix ex: <https://example.org/x#> .
    ex:c1 a lt:Compartment ; lt:Flag true . ex:c2 a lt:Compartment ; lt:Flag true, false .
    ex:c3 a lt:Compartment ; lt:Flag true, true .
    ex:c4 a lt:Compartment ; lt:Flag false, "no" .
    """
    # c4: both values disagree with `true` — still Unknown (several distinct values = ambiguous, not False)
    expected = {"c1": "True", "c2": "Unknown", "c3": "True", "c4": "Unknown"}
    assert _status_by_target(_run(CheckRuntime, rule, ttl)) == expected
    blind = BlindShaclRuleRunner().run_rule(
        graph_id="g",
        graph_uri="https://firebim.org/graphs/buildings/g",
        graph_ttl=ttl,
        rule_id=rid,
        rule_ttl=_ttl(rule),
    )
    assert {h["target_node"].split("#")[-1]: h["final_status"] for h in blind["end_outputs"]} == expected


# ------------------------------------------------------------------------------ aggregates + rdfs-light
UNIT_GRAPH = f"""
@prefix lt: <{LT}> . @prefix ex: <https://example.org/u#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
# schema subset (what merge_schema_graphs appends)
lt:Unit rdfs:subClassOf lt:FireSafetyUnit . lt:Compartment rdfs:subClassOf lt:FireSafetyUnit .
lt:Subcompartment rdfs:subClassOf lt:FireSafetyUnit .
lt:AreaDK rdfs:subPropertyOf lt:Area . lt:partOf owl:inverseOf lt:hasSubitem .
lt:CompartmentDK owl:equivalentClass lt:Compartment .
# data
ex:u1 a lt:Unit ; lt:hasSubitem ex:c1, ex:c2 .
ex:u2 a lt:Unit .
ex:c1 a lt:Compartment ; lt:RiskClass 2 ; lt:AreaDK 120 ; lt:hasSubitem ex:d1 .
ex:c2 a lt:CompartmentDK ; lt:RiskClass 3 ; lt:AreaDK 80 .
ex:d1 a lt:Door ; lt:IsFireExit true .
"""


def _unit_rule():
    rid = "t-unit"
    return {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-n", "label": "start"},
            {
                "id": f"{rid}-n",
                "element_type": "derive",
                "target_class": f"{LT}Unit",
                "property_path": f"{LT}NumberOfCompartments",
                "expression": f"count(${LT}hasSubitem)",
                "true_path": f"{rid}-has",
                "unknown_path": f"{rid}-ask",
                "label": "count compartments",
            },
            {
                "id": f"{rid}-has",
                "element_type": "decision",
                "target_class": f"{LT}Unit",
                "property_path": f"{LT}NumberOfCompartments",
                "operator": ">=",
                "value": 1,
                "true_path": f"{rid}-rc",
                "false_path": f"{rid}-empty",
                "unknown_path": f"{rid}-ask",
                "label": "at least one compartment?",
            },
            {
                "id": f"{rid}-rc",
                "element_type": "derive",
                "target_class": f"{LT}Unit",
                "property_path": f"{LT}RiskClass",
                "expression": f"max(${LT}hasSubitem/{LT}RiskClass)",
                "true_path": f"{rid}-area",
                "unknown_path": f"{rid}-ask",
                "label": "unit risk class = highest of its compartments",
            },
            {
                "id": f"{rid}-area",
                "element_type": "derive",
                "target_class": f"{LT}Unit",
                "property_path": f"{LT}TotalArea",
                "expression": f"sum(${LT}hasSubitem/{LT}Area)",  # Area is the super-property of AreaDK
                "true_path": f"{rid}-ok",
                "unknown_path": f"{rid}-ask",
                "label": "total area via super-property",
            },
            _end(rid, "ok", "pass"),
            _end(rid, "empty", "fail"),
            _end(rid, "ask", "missing_data"),
        ],
    }


def test_aggregates_and_rdfs_light_in_all_runners():
    rule = _unit_rule()
    for runtime_cls in (CheckRuntime, HorizontalLayeredCheckRuntime, VerticalPathwayCheckRuntime):
        result = _run(runtime_cls, rule, UNIT_GRAPH)
        assert _status_by_target(result) == {"u1": "True", "u2": "False"}
        derived = {
            (d["target"].split("#")[-1], d["property"].split("#")[-1]): d["value"] for d in result.derived_values
        }
        assert derived[("u1", "NumberOfCompartments")] == 2
        assert derived[("u1", "RiskClass")] == 3  # c2 is typed with the *equivalent* class and still counted
        assert derived[("u1", "TotalArea")] == 200  # read through rdfs:subPropertyOf
        assert derived[("u2", "NumberOfCompartments")] == 0  # count of nothing is 0, not Unknown
    blind = BlindShaclRuleRunner().run_rule(
        graph_id="g",
        graph_uri="https://firebim.org/graphs/buildings/g",
        graph_ttl=UNIT_GRAPH,
        rule_id="t-unit",
        rule_ttl=_ttl(rule),
        include_debug=True,
    )
    assert {h["target_node"].split("#")[-1]: h["final_status"] for h in blind["end_outputs"]} == {
        "u1": "True",
        "u2": "False",
    }


def test_subclass_targets_and_inverse_property():
    rid = "t-fsu"
    rule = {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-q", "label": "start"},
            {
                "id": f"{rid}-q",
                "element_type": "decision",
                "target_class": f"{LT}FireSafetyUnit",  # matches Unit, Compartment, CompartmentDK via the schema
                "property_path": f"{LT}partOf",  # only materialised through owl:inverseOf hasSubitem
                "operator": "exists",
                "value": True,
                "true_path": f"{rid}-part",
                "false_path": f"{rid}-top",
                "unknown_path": f"{rid}-ask",
                "label": "is part of something?",
            },
            _end(rid, "part", "pass"),
            _end(rid, "top", "fail"),
            _end(rid, "ask", "missing_data"),
        ],
    }
    result = _run(CheckRuntime, rule, UNIT_GRAPH)
    statuses = _status_by_target(result)
    assert set(statuses) == {"u1", "u2", "c1", "c2"}  # d1 (Door) is not a FireSafetyUnit
    assert statuses["c1"] == "True" and statuses["c2"] == "True"
    assert statuses["u1"] == "False" and statuses["u2"] == "False"


def test_rule_link_end_and_target_restriction():
    rid = "t-dispatch"
    rule = {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-q", "label": "which category?"},
            {
                "id": f"{rid}-q",
                "element_type": "decision",
                "target_class": f"{LT}Compartment",
                "property_path": f"{LT}Cat",
                "operator": "==",
                "value": "AK2",
                "true_path": f"{rid}-ak2",
                "false_path": f"{rid}-other",
                "unknown_path": f"{rid}-ask",
                "label": "AK2?",
            },
            {
                "id": f"{rid}-ak2",
                "element_type": "end",
                "label": "→ compartment size AK2",
                "config": {"end_kind": "rule", "next_rule": "dk-compartments-size-ak2", "output_text": "see AK2 rule"},
            },
            _end(rid, "other", "not_applicable"),
            _end(rid, "ask", "missing_data"),
        ],
    }
    errors, _ = lint_rule(rule)
    assert errors == []
    bad = {
        **rule,
        "elements": [
            *rule["elements"][:2],
            {**rule["elements"][2], "config": {"next_rule": rid}},
            *rule["elements"][3:],
        ],
    }
    assert any("links to its own rule" in e for e in lint_rule(bad)[0])

    ttl = _ttl(rule)
    back = {n["id"]: n for n in decompile_ruleset_ttl(ttl)["nodes"]}
    assert back[f"{rid}-ak2"]["config"]["next_rule"] == "dk-compartments-size-ak2"
    compiled = RuleCompiler().compile_from_ttl(ttl)
    assert compiled.nodes[f"{rid}-ak2"].next_rule == "dk-compartments-size-ak2"

    data = f"""
    @prefix lt: <{LT}> . @prefix ex: <https://example.org/x#> .
    ex:c1 a lt:Compartment ; lt:Cat "AK2" . ex:c2 a lt:Compartment ; lt:Cat "AK3" .
    ex:c3 a lt:Compartment ; lt:Cat "AK2" .
    """
    graph_ref = GraphRef(graph_id="g", graph_uri="https://firebim.org/graphs/buildings/g", ttl=data)
    result = CheckRuntime(evaluator=HybridAtomicEvaluator()).run_rule(
        graph_ref=graph_ref, compiled=compiled, options=RunOptions(include_end_node_targets=True)
    )
    by_target = {t.target_node.split("#")[-1]: (t.final_status, t.terminal_node_id) for t in result.targets}
    assert by_target["c1"] == ("True", f"{rid}-ak2") and by_target["c3"] == ("True", f"{rid}-ak2")
    assert by_target["c2"] == ("True", f"{rid}-other")
    end_meta = {e["node_id"]: e for e in result.compiled_rule["end_nodes"]}
    assert end_meta[f"{rid}-ak2"]["next_rule"] == "dk-compartments-size-ak2"

    # the RuleBook chains the next rule only for the targets that reached the link end
    restricted = CheckRuntime(evaluator=HybridAtomicEvaluator()).run_rule(
        graph_ref=graph_ref,
        compiled=compiled,
        options=RunOptions(target_nodes=["https://example.org/x#c1"]),
    )
    assert [t.target_node.split("#")[-1] for t in restricted.targets] == ["c1"]
    blind = BlindShaclRuleRunner().run_rule(
        graph_id="g",
        graph_uri="https://firebim.org/graphs/buildings/g",
        graph_ttl=data,
        rule_id=rid,
        rule_ttl=ttl,
        target_nodes=["https://example.org/x#c3"],
    )
    assert [h["target_node"].split("#")[-1] for h in blind["end_outputs"]] == ["c3"]


def test_aggregates_count_per_node_and_handle_booleans_and_codes():
    from rdflib import Graph

    from app.checks.derived import resolve_path_values
    from app.rules.expressions import evaluate, parse_expression

    g = Graph()
    g.parse(
        data=f"""
        @prefix lt: <{LT}> . @prefix ex: <https://example.org/x#> .
        ex:u lt:has ex:c1, ex:c2 . ex:c1 lt:has ex:d1, ex:d2 . ex:c2 lt:has ex:d3, ex:d1 .
        ex:d1 lt:Exit true . ex:d2 lt:Exit true . ex:d3 lt:Exit false .
        ex:c1 lt:RK "RK2" . ex:c2 lt:RK "RK3" .
        """,
        format="turtle",
    )
    segs = [f"{LT}has", f"{LT}has", f"{LT}Exit"]
    assert len(resolve_path_values(g, "https://example.org/x#u", segs)) == 2  # decisions: {true, false}
    assert len(resolve_path_values(g, "https://example.org/x#u", segs, distinct=False)) == 3  # d1 once, d2, d3
    many = {
        "e": [v.toPython() for v in resolve_path_values(g, "https://example.org/x#u", segs, distinct=False)],
        "rk": ["RK2", "RK3"],
    }
    assert evaluate(parse_expression("sum($e)"), None, lambda p: many[p]) == 2
    assert evaluate(parse_expression("max($rk)"), None, lambda p: many[p]) == "RK3"
    assert evaluate(parse_expression("all($e)"), None, lambda p: many[p]) is False
    assert evaluate(parse_expression("any($e)"), None, lambda p: many[p]) is True
    # nothing to aggregate: count() is 0, everything else is missing data (all() is never vacuously True)
    empty = {"e": []}
    assert evaluate(parse_expression("count($e)"), None, lambda p: empty[p]) == 0
    for fn in ("all", "any", "sum", "max"):
        with pytest.raises(ExpressionUnknown):
            evaluate(parse_expression(f"{fn}($e)"), None, lambda p: empty[p])


def test_check_response_reports_merged_schemas(api_client, monkeypatch):
    from types import SimpleNamespace

    from app.api import checks as checks_api

    summaries = [
        {"ontology_family": "fbo", "country": "lt", "canonical_country_uri": LT, "default_version": "2026.09.06"}
    ]
    schema_ttl = f"<{LT}Fg> <http://www.w3.org/2000/01/rdf-schema#subPropertyOf> <{FBO}Fg> ."
    fetched = SimpleNamespace(ttl=schema_ttl, version=SimpleNamespace(version="2026.09.06"))
    monkeypatch.setattr(checks_api, "_published_ontology_summaries", lambda: summaries)
    monkeypatch.setattr(checks_api._ontology_repo, "fetch_version", lambda family, country, version: fetched)

    res = api_client.post(
        "/checks/run", json={"rule_id": "t-fg", "graph_id": "lt", "options": {"include_trace": False}}
    )
    assert res.status_code == 200, res.text
    assert res.json()["engine"]["merged_schemas"] == ["fbo/lt/2026.09.06"]

    res = api_client.post("/checks/run", json={"rule_id": "t-fg", "graph_id": "lt", "options": {"schema": "none"}})
    assert res.json()["engine"]["merged_schemas"] == []


def test_missing_linked_rule_is_reported_as_rule_not_found(api_client, monkeypatch):
    from app.api import checks as checks_api
    from app.storage.graph_store import GraphStoreNotFoundError

    def missing(rule_id: str):
        raise GraphStoreNotFoundError(f"Graph not found: {rule_id}")

    monkeypatch.setattr(checks_api, "fetch_rule_ttl", missing)
    for endpoint in ("/checks/run", "/checks/run-diagnostic", "/checks/run-horisontal", "/checks/run-vertical"):
        res = api_client.post(endpoint, json={"rule_id": "dk-ak3", "graph_id": "lt"})
        assert res.status_code == 200, (endpoint, res.text)
        body = res.json()
        assert body["success"] is False and body["error"] == "rule_not_found", endpoint
        assert "dk-ak3" in body["message"]

    def missing_graph(graph_uri: str):
        raise GraphStoreNotFoundError(f"Graph not found: {graph_uri}")

    monkeypatch.setattr(checks_api, "fetch_rule_ttl", lambda rule_id: ("uri", _ttl(lt_rule())))
    monkeypatch.setattr(checks_api, "fetch_building_graph_ttl", missing_graph)
    body = api_client.post("/checks/run", json={"rule_id": "t-fg", "graph_id": "nope"}).json()
    assert body["error"] == "graph_not_found" and "nope" in body["message"]


def test_exists_after_a_derive_is_never_unknown_in_the_blind_runner():
    """A decision that follows a derive is evaluated lazily by the blind runner; `exists` must still answer
    True/False on presence, not Unknown when the path is absent."""
    rid = "t-lazy-exists"
    rule = {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-d", "label": "start"},
            {
                "id": f"{rid}-d",
                "element_type": "derive",
                "target_class": f"{LT}Compartment",
                "property_path": f"{LT}Flag",
                "expression": "1",
                "true_path": f"{rid}-q",
                "unknown_path": f"{rid}-ask",
                "label": "derive something first",
            },
            {
                "id": f"{rid}-q",
                "element_type": "decision",
                "target_class": f"{LT}Compartment",
                "property_path": f"{LT}Lighting",
                "operator": "exists",
                "true_path": f"{rid}-ok",
                "false_path": f"{rid}-no",
                "unknown_path": f"{rid}-ask",
                "label": "lighting present?",
            },
            _end(rid, "ok", "pass"),
            _end(rid, "no", "fail"),
            _end(rid, "ask", "missing_data"),
        ],
    }
    ttl = f"""
    @prefix lt: <{LT}> . @prefix ex: <https://example.org/x#> .
    ex:c1 a lt:Compartment ; lt:Lighting ex:sys1, ex:sys2 . ex:c2 a lt:Compartment .
    """
    expected = {"c1": "True", "c2": "False"}
    assert _status_by_target(_run(CheckRuntime, rule, ttl)) == expected
    blind = BlindShaclRuleRunner().run_rule(
        graph_id="g",
        graph_uri="https://firebim.org/graphs/buildings/g",
        graph_ttl=ttl,
        rule_id=rid,
        rule_ttl=_ttl(rule),
    )
    assert {h["target_node"].split("#")[-1]: h["final_status"] for h in blind["end_outputs"]} == expected


def test_rule_title_round_trip_and_fallback(api_client):
    from app.rules.publisher import display_title

    assert display_title(None, "Start: Flow-CompartmentsSize AK5 (v2)") == "Flow-CompartmentsSize AK5 (v2)"
    assert display_title(None, "Starting point") == "Starting point"
    assert display_title(" Miro title ", "Start: x") == "Miro title"

    rule = lt_rule()
    rule["title"] = "What is the maximum area of fire compartments? (LT)"
    rule["description"] = "Fg formula"
    rule["source"] = {"system": "miro", "frame_title": "Fg"}
    res = api_client.post("/rules/compile", json={"rule": rule})
    assert res.status_code == 200, res.text
    back = api_client.post("/rules/decompile", json={"ttl": res.json()["ttl"]}).json()["rule"]
    assert back["title"] == rule["title"]
    assert back["description"] == "Fg formula"
    assert back["source"] == {"system": "miro", "frame_title": "Fg"}

    # no explicit title: the start label without "Start:" is the title
    rule = lt_rule()
    rule["elements"][0]["label"] = "Start: Fg check"
    res = api_client.post("/rules/compile", json={"rule": rule})
    back = api_client.post("/rules/decompile", json={"ttl": res.json()["ttl"]}).json()["rule"]
    assert back["title"] == "Fg check"


# ------------------------------------------------------------------------------ answers: questions + overrides
def test_open_questions_name_the_missing_fact_and_where_to_write_it():
    from app.checks.assertions import open_questions

    rid = "t-q"
    rule = {
        "id": rid,
        "entry": f"{rid}-start",
        "elements": [
            {"id": f"{rid}-start", "element_type": "start", "true_path": f"{rid}-spr", "label": "start"},
            {
                "id": f"{rid}-spr",
                "element_type": "decision",
                "target_class": f"{LT}Compartment",
                "property_path": f"{LT}partOf/{LT}Sprinklered",  # declared on the unit
                "operator": "==",
                "value": True,
                "true_path": f"{rid}-ak",
                "false_path": f"{rid}-ak",
                "unknown_path": f"{rid}-ask",
                "label": "Sprinklered? (unit)",
            },
            {
                "id": f"{rid}-ak",
                "element_type": "decision",
                "target_class": f"{LT}Compartment",
                "property_path": f"{LT}UseCategory",
                "operator": "==",
                "value": "AK2",
                "true_path": f"{rid}-area",
                "false_path": f"{rid}-area",
                "unknown_path": f"{rid}-ask",
                "label": "AK2?",
            },
            {
                "id": f"{rid}-area",
                "element_type": "decision",
                "target_class": f"{LT}Compartment",
                "property_path": f"{LT}Area",
                "operator": "<=",
                "value": 2000,
                "true_path": f"{rid}-ok",
                "false_path": f"{rid}-no",
                "unknown_path": f"{rid}-ask",
                "label": "area ok?",
            },
            _end(rid, "ok", "pass"),
            _end(rid, "no", "fail"),
            _end(rid, "ask", "missing_data"),
        ],
    }
    ttl = f"""
    @prefix lt: <{LT}> . @prefix ex: <https://example.org/q#> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    lt:partOf owl:inverseOf lt:has .
    ex:u1 a lt:Unit ; lt:has ex:c1, ex:c2, ex:c3 ; lt:Sprinklered false .
    ex:c1 a lt:Compartment ; lt:UseCategory "AK2" .              # area missing -> number question
    ex:c2 a lt:Compartment ; lt:UseCategory "AK2", "AK3" .       # ambiguous -> choice question
    ex:c3 a lt:Compartment ; lt:UseCategory "AK2" ; lt:Area 100 .
    ex:c4 a lt:Compartment ; lt:UseCategory "AK2" ; lt:Area 100 . # no unit -> boolean question, nowhere to write
    """
    result = _run(CheckRuntime, rule, ttl)
    assert _status_by_target(result) == {"c1": "Unknown", "c2": "Unknown", "c3": "True", "c4": "Unknown"}
    questions = open_questions(result=result, graph_ttl=ttl, compiled=RuleCompiler().compile_from_ttl(_ttl(rule)))
    by_target = {q["target_node"].split("#")[-1]: q for q in questions}
    assert set(by_target) == {"c1", "c2", "c4"}
    assert by_target["c1"]["kind"] == "number" and by_target["c1"]["write_property"] == f"{LT}Area"
    assert by_target["c1"]["write_subject"].endswith("#c1")
    assert by_target["c2"]["kind"] == "choice" and set(by_target["c2"]["options"]) >= {"AK2", "AK3"}
    assert by_target["c4"]["kind"] == "boolean" and by_target["c4"]["options"] == [True, False]
    assert by_target["c4"]["write_subject"] is None and "not part of any unit" in by_target["c4"]["problem"]
    assert by_target["c4"]["write_property"] == f"{LT}Sprinklered"


def test_overrides_and_graph_answers_endpoint(api_client, monkeypatch):
    from app.api import graphs as graphs_api

    # c3 has no FireCompartmentHeight / HabsCalculatedAltitude -> the Fg derive asks for them
    res = api_client.post("/checks/run", json={"rule_id": "t-fg", "graph_id": "lt", "options": {}})
    body = res.json()
    assert body["summary"]["unknown"] == 1
    asks = [q for q in body["questions"] if q["target_node"].endswith("#c3")]
    assert asks and asks[0]["node_type"] == "derive" and asks[0]["kind"] == "number"
    missing = asks[0]["write_property"]
    assert missing in (f"{LT}FireCompartmentHeight", f"{LT}HabsCalculatedAltitude")

    # answer for this run only: overrides
    overrides = [
        {"subject": "https://example.org/building/7#c3", "property": f"{LT}FireCompartmentHeight", "value": 12},
        {"subject": "https://example.org/building/7#c3", "property": f"{LT}HabsCalculatedAltitude", "value": 28},
    ]
    res = api_client.post(
        "/checks/run", json={"rule_id": "t-fg", "graph_id": "lt", "options": {"overrides": overrides}}
    )
    assert res.json()["summary"]["unknown"] == 0 and res.json()["questions"] == []

    # answer persistently: an answered copy of the graph
    store: dict[str, str] = {"lt": LT_GRAPH}

    class FakeRepo:
        def fetch_graph(self, graph_id):
            from app.storage.graph_store import GraphStoreNotFoundError

            if graph_id not in store:
                raise GraphStoreNotFoundError(graph_id)
            return f"https://firebim.org/graphs/buildings/{graph_id}", store[graph_id]

        def publish_graph(self, graph_id, ttl):
            existed = graph_id in store
            store[graph_id] = ttl
            return f"https://firebim.org/graphs/buildings/{graph_id}", existed

    monkeypatch.setattr(graphs_api, "repo", FakeRepo())
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth.jwt import get_current_active_user

    app = FastAPI()
    app.include_router(graphs_api.router)
    app.dependency_overrides[get_current_active_user] = lambda: object()
    client = TestClient(app)
    res = client.post("/graphs/lt/answers", json={"assertions": overrides[:1]})
    assert res.status_code == 200, res.text
    assert res.json()["graph_id"] == "lt--answered" and res.json()["created"] is True
    res = client.post("/graphs/lt/answers", json={"assertions": overrides[1:]})
    assert res.json()["created"] is False and res.json()["applied"] == 1
    answered = store["lt--answered"]
    assert "FireCompartmentHeight" in answered and "answeredFromGraph" in answered
    assert "https://firebim.org/graphs/buildings/lt>" in answered  # provenance points at the base graph
    assert "lt" in store and store["lt"] == LT_GRAPH  # the base graph is untouched
    # the base graph is never overwritten, not even on request
    assert (
        client.post("/graphs/lt/answers", json={"assertions": overrides, "answered_graph_id": "lt"}).status_code == 400
    )


def test_standalone_derive_element_publishes_and_bad_elements_are_400(api_client, monkeypatch):
    from app.api import rules as rules_api

    monkeypatch.setattr(rules_api, "publish_graph_ttl", lambda **kwargs: None)
    element = {
        "id": "derive-example",
        "element_type": "derive",
        "target_class": f"{DK}FireCompartment",
        "property_path": f"{DK}MaxAreaFireCompartment",
        "expression": "2000",
        "label": "Derive — permitted area",
        "config": {"library_category": "Examples"},
    }
    res = api_client.post("/rules/rule-elements", json=element)  # a library element has no paths yet
    assert res.status_code == 200 and res.json()["published"] is True, res.text
    res = api_client.post("/rules/rule-elements", json={**element, "expression": "2000 +"})
    assert res.status_code == 400 and "expression" in res.json()["detail"]

    # in a rule, a derive without true_path is a lint error
    rule = lt_rule()
    rule["elements"][2]["true_path"] = None
    res = api_client.post("/rules/validate", json={"rule": rule})
    assert res.json()["valid"] is False and any("true_path" in e for e in res.json()["errors"])
