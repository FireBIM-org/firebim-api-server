from app.rules.decompiler import decompile_rule_element_ttl
from app.rules.dsl import Rule
from app.rules.shacl_builder import SHACLBuilder


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


def test_uri_equality_roundtrips_as_uri_not_literal():
    builder = SHACLBuilder()

    rule = (
        Rule("window_type_rule")
        .target(FakeFBO.Window)
        .when("http://example.org/fbo/1.0/WindowType")
        .eq("https://example.org/types/fire-rated")
        .true("ok")
        .false("fail")
        .unknown("missing")
        .build()
    )

    builder.add_rule_element(rule)
    ttl = builder.graph.serialize(format="turtle")
    decompiled = decompile_rule_element_ttl(ttl)

    assert "<https://example.org/types/fire-rated>" in ttl
    assert str(decompiled["value"]) == "https://example.org/types/fire-rated"
