from app.rules.shacl_builder import SHACLBuilder
from app.rules.dsl import Rule


class FakeFBO:
    Window = "http://example.org/fbo/1.0/Window"
    FireLoadDensityModified = "http://example.org/fbo/1.0/FireLoadDensityModified"


def test_ttl_generation():

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

    ttl = builder.graph.serialize(format="turtle")

    assert "sh:NodeShape" in ttl
    assert "window_fire_load_rule" in ttl
    assert "FireLoadDensityModified" in ttl
