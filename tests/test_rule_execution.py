from rdflib import Graph
from pyshacl import validate

from app.rules.shacl_builder import SHACLBuilder
from app.rules.dsl import Rule


class FakeFBO:
    Window = "http://example.org/fbo/1.0/Window"
    FireLoadDensityModified = "http://example.org/fbo/1.0/FireLoadDensityModified"


def test_high_fire_load_rule():

    data = """
    @prefix ex: <http://example.org/firebim/> .
    @prefix fbo: <http://example.org/fbo/1.0/> .

    ex:W1 a ex:Window ;
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

    conforms, results_graph, results_text = validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="rdfs",
        advanced=True,
    )

    assert conforms