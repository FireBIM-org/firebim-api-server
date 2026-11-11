from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Scalar = str | bool | int | float
Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=200)]
IRI = Annotated[
    str, StringConstraints(pattern=r"^(?:https?://[^\s<>\"{}|^`\\]+|urn:[^\s<>\"{}|^`\\]+)$", max_length=2000)
]


class MappingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, serialize_by_alias=True, allow_inf_nan=False)


class EntitySelector(MappingModel):
    schema_: Literal["IFC4"] = Field(alias="schema")
    ifc_class: Name
    match: Literal["exact", "subtypes"]
    predefined_type: Name | None = None


class EntityUnionSelector(MappingModel):
    schema_: Literal["IFC4"] = Field(alias="schema")
    any_of: list[EntitySelector] = Field(min_length=2, max_length=32)


def source_selectors(source: EntitySelector | EntityUnionSelector) -> tuple[EntitySelector, ...]:
    """Return the full, flat selectors for either source shape, in declared order."""
    return tuple(source.any_of) if isinstance(source, EntityUnionSelector) else (source,)


class ValueSelector(MappingModel):
    kind: Literal["attribute", "property", "quantity"]
    name: Name
    pset: Name | None = None
    datatype: IRI
    required: bool = False
    unit: Literal["unitless", "ifc_length", "ifc_area"] | None = None

    @model_validator(mode="after")
    def check_property_context(self):
        if (self.kind in {"property", "quantity"}) != (self.pset is not None):
            raise ValueError("Property and quantity selectors require an exact pset; attributes cannot specify one")
        return self


class LookupEntry(MappingModel):
    input: Scalar
    output: Scalar


class ConstantValue(MappingModel):
    kind: Literal["constant"]
    value: Scalar


class ReadValue(MappingModel):
    kind: Literal["read"]
    selector: ValueSelector
    fallback: list[ValueSelector] = Field(default_factory=list, max_length=4)
    transform: Literal["identity", "lookup", "numeric_cast"] = "identity"
    lookup: list[LookupEntry] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_lookup(self):
        if (self.transform == "lookup") != bool(self.lookup):
            raise ValueError("A nonempty lookup table is required only for lookup transforms")
        if self.fallback and (
            self.selector.unit not in {"ifc_length", "ifc_area"}
            or any(
                (selector.datatype, selector.unit) != (self.selector.datatype, self.selector.unit)
                for selector in self.fallback
            )
        ):
            raise ValueError("Fallback reads require measurement selectors with identical datatype and unit")
        return self


class LeafCondition(MappingModel):
    kind: Literal["present", "equals", "in"]
    selector: ValueSelector
    values: list[Scalar] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_values(self):
        if self.kind == "present" and self.values:
            raise ValueError("Presence tests do not take values")
        if self.kind == "equals" and len(self.values) != 1:
            raise ValueError("Equality tests require one typed value")
        if self.kind == "in" and not self.values:
            raise ValueError("Membership tests require values")
        return self


class CompoundCondition(MappingModel):
    kind: Literal["all_of", "any_of"]
    conditions: list[Condition] = Field(min_length=1, max_length=32)


Condition = Annotated[LeafCondition | CompoundCondition, Field(discriminator="kind")]
CompoundCondition.model_rebuild()


class ClassAssertion(MappingModel):
    kind: Literal["class"]
    iri: IRI
    grouping: ValueSelector | None = None


class DatatypeAssertion(MappingModel):
    kind: Literal["datatype_property"]
    iri: IRI
    datatype: IRI
    value: Annotated[ConstantValue | ReadValue, Field(discriminator="kind")]
    cardinality: Literal["single", "many"]


class ObjectAssertion(MappingModel):
    kind: Literal["object_property"]
    iri: IRI
    object: Literal["relating", "related"]
    cardinality: Literal["single", "many"]


class MappingPolicies(MappingModel):
    identity: Literal["model_scoped"]
    property_precedence: Literal["occurrence_over_type"]
    missing: Literal["skip"]
    invalid: Literal["error"]
    conflicts: Literal["error"]


class ExecutableMapping(MappingModel):
    language: Literal["ifc4-mapping/1"]
    source: EntitySelector | EntityUnionSelector
    subject: Literal["entity", "relating", "related"]
    target: Annotated[ClassAssertion | DatatypeAssertion | ObjectAssertion, Field(discriminator="kind")]
    condition: Condition | None = None
    policies: MappingPolicies
