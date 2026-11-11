from dataclasses import FrozenInstanceError

import pytest

from app.lexicon.ifc_mapping_evaluator import Assertion
from app.lexicon.ifc_mapping_units import Unit, parse_model_units, physical_key, source_value

XSD = "http://www.w3.org/2001/XMLSchema#"


def test_units_and_assertions_are_immutable_and_unitless_remains_distinct():
    unit = Unit("m2", "area", 1)
    assertion = Assertion("subject", "predicate", 1, unit=unit)
    with pytest.raises(FrozenInstanceError):
        unit.scale_to_si = 2
    with pytest.raises(FrozenInstanceError):
        assertion.unit = None
    assert physical_key(1, unit) == physical_key(10000, Unit("cm2", "area", 0.0001))
    assert physical_key(1, unit) != physical_key(1, None)
    assert physical_key(1, None) != physical_key(True, None)
    assert physical_key(1, unit) != physical_key(1, Unit("m", "length", 1))


def test_squared_length_scale_compares_without_binary_multiplication_artifacts():
    units = parse_model_units({"length": {"symbol": "dm", "dimension": "length", "scale_to_si": 0.1}})
    value, unit, origin = source_value(100, XSD + "integer", "ifc_area", units)
    assert (unit.symbol, unit.scale_to_si, origin) == ("dm2", 0.01, "model_length_squared")
    assert physical_key(value, unit) == physical_key(1, Unit("m2", "area", 1))


def test_integer_measurements_outside_finite_range_are_rejected():
    with pytest.raises(ValueError):
        source_value(10**400, XSD + "integer", "ifc_area", {})


@pytest.mark.parametrize("unit_kind", ["unitless", None])
def test_unit_wrappers_are_not_valid_for_unitless_values(unit_kind):
    with pytest.raises(ValueError):
        source_value(
            {"value": 1, "unit": {"symbol": "m", "dimension": "length", "scale_to_si": 1}},
            XSD + "integer",
            unit_kind,
            {},
        )
