from app.rules.dsl import Rule, Property
from app.rules.models import fbRuleElement


class FakeFBO:
    FireLoadDensityModified = "http://example.org/fbo/1.0/FireLoadDensityModified"
    Window = "http://example.org/fbo/1.0/Window"


def test_rule_creation():

    rule = (
        Rule("window_fire_load_rule")
        .target(FakeFBO.Window)
        .when(FakeFBO.FireLoadDensityModified)
        .gt(20000)
        .true("high_fire")
        .false("normal")
        .unknown("missing")
        .build()
    )

    assert isinstance(rule, fbRuleElement)

    assert rule.id == "window_fire_load_rule"
    assert rule.operator == ">"
    assert rule.value == 20000
    assert rule.true_path == "high_fire"
