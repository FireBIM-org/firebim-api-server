"""Units for already extracted numeric fixtures; never infer measurements from geometry."""

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from app.lexicon.ifc_mapping_values import valid_literal


@dataclass(frozen=True)
class Unit:
    symbol: str
    dimension: Literal["length", "area"]
    scale_to_si: int | float

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("Unit symbols must be nonempty strings")
        if not isinstance(self.dimension, str) or self.dimension not in {"length", "area"}:
            raise ValueError("Only length and area units are supported")
        if type(self.scale_to_si) not in (int, float) or self.scale_to_si <= 0:
            raise ValueError("Unit scales must be positive finite numbers")
        try:
            if not math.isfinite(self.scale_to_si):
                raise ValueError("Unit scales must be positive finite numbers")
        except OverflowError as exc:
            raise ValueError("Unit scale is outside the finite numeric range") from exc


def parse_unit(value, dimension: str) -> Unit:
    if not isinstance(value, dict) or set(value) != {"symbol", "dimension", "scale_to_si"}:
        raise ValueError("Units require exactly symbol, dimension and scale_to_si")
    if value["dimension"] != dimension:
        raise ValueError("Unit dimension does not match the measurement")
    return Unit(**value)


def _square(unit: Unit) -> Unit:
    try:
        scale = float(Fraction(str(unit.scale_to_si)) ** 2)
        return Unit(unit.symbol + "2", "area", scale)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Squared length scale must remain positive and finite") from exc


def parse_model_units(model_units: dict | None) -> dict[str, Unit]:
    if model_units is None:
        return {}
    if not isinstance(model_units, dict) or set(model_units) - {"length", "area"}:
        raise ValueError("Model units must be keyed only by length and area")
    units = {dimension: parse_unit(value, dimension) for dimension, value in model_units.items()}
    if "length" in units and "area" not in units:
        _square(units["length"])
    return units


def source_value(value, datatype: str, unit_kind: str | None, model_units: dict[str, Unit]):
    unit = None
    origin = None
    if unit_kind in {"ifc_length", "ifc_area"}:
        dimension = unit_kind.removeprefix("ifc_")
        if isinstance(value, dict):
            if set(value) != {"value", "unit"}:
                raise ValueError("Measurement wrappers require exactly value and unit")
            unit = parse_unit(value["unit"], dimension)
            value = value["value"]
            origin = "value"
        elif dimension in model_units:
            unit, origin = model_units[dimension], "model"
        elif dimension == "area" and "length" in model_units:
            unit, origin = _square(model_units["length"]), "model_length_squared"
        else:
            unit, origin = Unit("m" if dimension == "length" else "m2", dimension, 1), "si_default"
        if type(value) not in (int, float):
            raise ValueError("Measurement values must be typed numbers, not strings or Booleans")
        try:
            if not math.isfinite(value):
                raise ValueError("Measurement values must be finite")
        except OverflowError as exc:
            raise ValueError("Measurement value is outside the finite numeric range") from exc
    if not valid_literal(value, datatype):
        raise ValueError("Source value does not have its declared datatype")
    return value, unit, origin


def physical_key(value, unit: Unit | None):
    if unit is None:
        return None, type(value).__name__, value
    return unit.dimension, Fraction(str(value)) * Fraction(str(unit.scale_to_si))
