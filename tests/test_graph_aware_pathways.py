from __future__ import annotations

from app.checks.compiler import RuleCompiler
from app.checks.evaluator_base import NoOpEvaluator
from app.checks.execution_state import ExecutionContext
from app.checks.graph_index import GraphIndexBuilder
from app.checks.pathways import PathwayResolver
from app.checks.planner import RuleExecutionPlanner
from app.checks.result_models import CompiledRuleSet, DecisionNode, GraphRef, RunOptions, TargetRef
from app.checks.runtime import CheckRuntime
from app.rules.engine import RuleEngine
from app.rules.models import fbRule, fbRuleElement
from app.rules.shacl_builder import SHACLBuilder

TTL = """
@prefix ex: <http://example.org/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

ex:B1 rdf:type ex:Building ;
    ex:contains ex:W1 .
ex:B2 rdf:type ex:Building ;
    ex:contains ex:W2 .

ex:W1 rdf:type ex:Wall ;
    ex:hasWindow ex:Win1 .
ex:W2 rdf:type ex:Wall ;
    ex:hasWindow ex:Win2 .

ex:Win1 rdf:type ex:Window .
ex:Win2 rdf:type ex:Window .
"""


def _compiled_rule() -> CompiledRuleSet:
    return CompiledRuleSet(
        rule_id="graph-aware-test",
        entry_node_id="building-check",
        nodes={
            "building-check": DecisionNode(
                node_id="building-check",
                target_class="http://example.org/Building",
                property_path="http://example.org/contains",
                operator="==",
                value="ignored",
                true_node_id="wall-check",
                false_node_id="wall-check",
                unknown_node_id="wall-check",
            ),
            "wall-check": DecisionNode(
                node_id="wall-check",
                target_class="http://example.org/Wall",
                property_path="http://example.org/hasWindow",
                operator="==",
                value="ignored",
                true_node_id="window-check",
                false_node_id="window-check",
                unknown_node_id="window-check",
            ),
            "window-check": DecisionNode(
                node_id="window-check",
                target_class="http://example.org/Window",
                property_path="http://example.org/name",
                operator="==",
                value="ignored",
                true_node_id="window-check",
                false_node_id="window-check",
                unknown_node_id="window-check",
            ),
        },
    )


def _execution_context() -> tuple[ExecutionContext, CompiledRuleSet]:
    graph_ref = GraphRef(
        graph_id="g1",
        graph_uri="https://firebim.org/graphs/buildings/g1",
        ttl=TTL,
    )
    compiled = _compiled_rule()
    graph_index = GraphIndexBuilder().build(graph_ref=graph_ref)
    options = RunOptions()
    plan = RuleExecutionPlanner().plan(compiled=compiled, schema=graph_index.schema, options=options)
    return (
        ExecutionContext(
            graph_ref=graph_ref,
            compiled=compiled,
            options=options,
            graph_cache_key=graph_index.graph_cache_key,
            graph_index=graph_index,
            schema=graph_index.schema,
            plan=plan,
        ),
        compiled,
    )


def test_pathway_resolver_filters_cross_class_targets_by_graph_connectivity():
    execution_context, compiled = _execution_context()
    resolver = PathwayResolver()

    batch = resolver.resolve_forward(
        current_targets=[TargetRef(target_node="http://example.org/B1", target_class="http://example.org/Building")],
        from_node=compiled.get_node("building-check"),
        to_node=compiled.get_node("wall-check"),
        execution_context=execution_context,
    )

    assert [target.target_node for target in batch.targets] == ["http://example.org/W1"]


def test_pathway_resolver_can_walk_backwards_without_global_class_scan():
    execution_context, compiled = _execution_context()
    resolver = PathwayResolver()

    batch = resolver.resolve_backward(
        current_targets=[TargetRef(target_node="http://example.org/Win1", target_class="http://example.org/Window")],
        from_node=compiled.get_node("window-check"),
        to_node=compiled.get_node("wall-check"),
        execution_context=execution_context,
    )

    assert [target.target_node for target in batch.targets] == ["http://example.org/W1"]


def test_pathway_resolver_can_follow_reverse_edge_during_forward_execution():
    execution_context, compiled = _execution_context()
    resolver = PathwayResolver()

    batch = resolver.resolve_forward(
        current_targets=[TargetRef(target_node="http://example.org/Win1", target_class="http://example.org/Window")],
        from_node=compiled.get_node("window-check"),
        to_node=compiled.get_node("wall-check"),
        execution_context=execution_context,
    )

    assert [target.target_node for target in batch.targets] == ["http://example.org/W1"]
    assert batch.resolution_mode == "reverse_edge"


def test_terminal_value_passed_forces_true_end_status():
    rule = fbRule(id="terminal-rule", entry_element="start")
    rule.entry = "start"
    rule.add_element(
        fbRuleElement(
            id="start",
            node_type="start",
            true_path="pass-end",
            label="start",
        )
    )
    rule.add_element(
        fbRuleElement(
            id="pass-end",
            node_type="end",
            label="assembled-pass",
            config={"terminal_value": "passed"},
        )
    )
    builder = SHACLBuilder()
    builder.add_rule_set(rule)
    compiled = RuleCompiler().compile_from_ttl(builder.get_graph().serialize(format="turtle"))
    runtime = CheckRuntime(evaluator=NoOpEvaluator())
    end_node = compiled.get_node("pass-end")
    assert end_node.node_type == "end"
    assert runtime._infer_end_status(end_node, incoming_status="False") == "True"


def test_fb_rule_entry_alias_stays_in_sync_with_entry_element():
    rule = fbRule(id="alias-rule", entry_element="start")
    assert rule.entry == "start"

    rule.entry = "next-start"

    assert rule.entry_element == "next-start"
    assert rule.entry == "next-start"


def test_rule_engine_explain_without_previous_decision_uses_current_context():
    rule = fbRule(id="explain-rule", entry_element="explain")
    rule.add_element(
        fbRuleElement(
            id="explain",
            node_type="control",
            operator="explain",
            target_class="http://example.org/fbo/1.0/Room",
            property_path="http://example.org/fbo/1.0/roomArea",
            value=12,
        )
    )

    engine = RuleEngine(rule_set=rule, evaluator=lambda element, data_graph: "Unknown")
    result = engine.execute()

    assert result["status"] == "Unknown"
    assert result["rule_id"] == "explain"
    assert result["target"] == "http://example.org/fbo/1.0/Room"
    assert result["condition"]["property"] == "http://example.org/fbo/1.0/roomArea"


def test_rule_engine_end_node_returns_terminal_value_without_evaluator_result():
    rule = fbRule(id="end-rule", entry_element="done")
    rule.add_element(
        fbRuleElement(
            id="done",
            node_type="end",
            label="complete",
            config={"terminal_value": "passed"},
        )
    )

    engine = RuleEngine(rule_set=rule, evaluator=lambda element, data_graph: "Unknown")

    assert engine.execute() == "passed"
