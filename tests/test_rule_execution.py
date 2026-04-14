import pytest
from rdflib import Graph, Namespace

from app.rules.dsl import Rule
from app.rules.shacl_builder import SHACLBuilder


class FakeFBO:
    Window = "http://example.org/fbo/1.0/Window"
    FireLoadDensityModified = "http://example.org/fbo/1.0/FireLoadDensityModified"


@pytest.mark.skip(reason="Feature-specific rule execution test not in current PR scope")
def test_high_fire_load_rule():
    from pyshacl import validate

    data = """
    @prefix ex: <http://example.org/firebim/> .
    @prefix fbo: <http://example.org/fbo/1.0/> .
    @prefix fb: <http://example.org/firebim/rule/> .

    ex:W1 a fbo:Window ;
          fbo:FireLoadDensityModified 25000 .
    """

    builder = SHACLBuilder()

    rule = (
        Rule("window_fire_load_rule")
        .target(FakeFBO.Window)
        .when(FakeFBO.FireLoadDensityModified)
        .gt(20000)
        .true("high")
        .false("normal")
        .unknown("missing")
        .build()
    )

    builder.add_rule_element(rule)

    shapes_ttl = builder.get_graph().serialize(format="turtle")

    data_graph = Graph().parse(data=data, format="turtle")
    shapes_graph = Graph().parse(data=shapes_ttl, format="turtle")

    conforms, results_graph, _results_text = validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="rdfs",
        advanced=True,
    )

    assert conforms

    fb = Namespace("http://example.org/firebim/rule/")
    ex = Namespace("http://example.org/firebim/")
    assert (ex.W1, fb.ruleResult, fb["True"]) in results_graph
