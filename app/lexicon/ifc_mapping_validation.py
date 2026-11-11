from __future__ import annotations

from pydantic import ValidationError
from rdflib import URIRef
from rdflib.namespace import OWL, RDF, RDFS

from app.lexicon.ifc4_schema import attribute_datatype, is_entity, is_subtype, predefined_types
from app.lexicon.ifc_mapping_values import DATATYPES, NUMERIC_DATATYPES, XSD, valid_literal
from app.schemas.ifc_mapping import (
    ClassAssertion,
    CompoundCondition,
    ConstantValue,
    DatatypeAssertion,
    EntityUnionSelector,
    ExecutableMapping,
    ObjectAssertion,
    source_selectors,
)
from app.schemas.translation import TranslationValidationDiagnostic

RANGE_PREDICATES = (RDFS.range, URIRef("http://schema.org/rangeIncludes"), URIRef("https://schema.org/rangeIncludes"))
TARGET_KINDS = {
    OWL.Class: "class",
    RDFS.Class: "class",
    OWL.DatatypeProperty: "datatype_property",
    OWL.ObjectProperty: "object_property",
}
RELATIONSHIPS = {"IfcRelAggregates", "IfcRelContainedInSpatialStructure"}
UNIT_PREDICATES = tuple(
    URIRef(iri)
    for iri in (
        "http://qudt.org/schema/qudt/unit",
        "http://www.ontology-of-units-of-measure.org/resource/om-2/hasUnit",
        "http://schema.org/unitCode",
        "https://schema.org/unitCode",
        "http://schema.org/unitText",
        "https://schema.org/unitText",
    )
)


def validate_executable_mapping(definition, modules, mapping_id, path):
    diagnostics = []

    def error(code, field, message):
        diagnostics.append(
            TranslationValidationDiagnostic(
                code=code,
                severity="error",
                mapping_id=mapping_id,
                field_path=f"{path}.{field}",
                message=message,
            )
        )

    if definition.get("language") != "ifc4-mapping/1":
        error(
            "mapping_language_unsupported",
            "language",
            "Use ifc4-mapping/1 or an unchanged, reviewed module suggestion with verified provenance.",
        )
        return None, diagnostics
    try:
        mapping = ExecutableMapping.model_validate(definition)
    except ValidationError as exc:
        for item in exc.errors(include_url=False, include_input=False)[:20]:
            error("mapping_definition_invalid", ".".join(map(str, item["loc"])), item["msg"])
        return None, diagnostics

    source = mapping.source
    sources = source_selectors(source)
    source_fields = [
        f"source.any_of[{index}]" if isinstance(source, EntityUnionSelector) else "source"
        for index in range(len(sources))
    ]
    if mapping.target.iri == "https://firebim.org/ontologies/fbo#FireResistance":
        error(
            "mapping_interpretation_required",
            "target.iri",
            "FBO fire-resistance semantics are unresolved for this pilot; "
            "keep this target disabled, including constant mappings.",
        )
    for member, field in zip(sources, source_fields, strict=True):
        if not is_entity(member.ifc_class):
            error(
                "mapping_source_class_unsupported", field + ".ifc_class", "Select a supported, exact IFC4 entity name."
            )
        if member.predefined_type and member.predefined_type not in predefined_types(member.ifc_class):
            error(
                "mapping_predefined_type_invalid",
                field + ".predefined_type",
                "This enum value is not declared for the IFC4 class.",
            )
    target = mapping.target
    relation = any(member.ifc_class in RELATIONSHIPS for member in sources)
    if isinstance(target, ObjectAssertion):
        if (
            isinstance(source, EntityUnionSelector)
            or not relation
            or source.match != "exact"
            or mapping.subject not in {"relating", "related"}
            or (mapping.subject == target.object or mapping.condition is not None or source.predefined_type is not None)
        ):
            error(
                "mapping_relationship_unsupported",
                "subject",
                "Object assertions require one exact IfcRelAggregates or IfcRelContainedInSpatialStructure, "
                "opposite relating/related endpoints, and no condition or predefined type. No traversal is inferred.",
            )
    elif (
        relation
        or any(is_subtype(member.ifc_class, "IfcRelationship") for member in sources)
        or mapping.subject != "entity"
    ):
        error(
            "mapping_subject_invalid",
            "subject",
            "Class and value assertions bind the selected entity, not relationship endpoints.",
        )

    iri = URIRef(target.iri)
    kinds = {
        TARGET_KINDS[kind] for module in modules for kind in module.graph.objects(iri, RDF.type) if kind in TARGET_KINDS
    }
    if not kinds:
        error(
            "mapping_target_not_declared",
            "target.iri",
            "The full target IRI is not explicitly declared in the selected releases.",
        )
    elif kinds != {target.kind}:
        error(
            "mapping_target_kind_mismatch",
            "target.kind",
            "The assertion kind must match the unambiguous destination declaration.",
        )
    ranges = {
        value for module in modules for predicate in RANGE_PREDICATES for value in module.graph.objects(iri, predicate)
    }
    if isinstance(target, (DatatypeAssertion, ObjectAssertion)):
        if len(ranges) > 1 or any(not isinstance(value, URIRef) for value in ranges):
            error(
                "mapping_target_range_ambiguous",
                "target.iri",
                "Multiple or anonymous ranges are unsupported; select an unambiguous declaration.",
            )
        if target.cardinality == "many" and any(
            (iri, RDF.type, OWL.FunctionalProperty) in module.graph for module in modules
        ):
            error("mapping_cardinality_invalid", "target.cardinality", "A functional property must be single-valued.")
    if isinstance(target, ObjectAssertion) and len(ranges) == 1:
        range_iri = next(iter(ranges))
        if range_iri != OWL.Thing and not any(
            (range_iri, RDF.type, kind) in module.graph for module in modules for kind in (OWL.Class, RDFS.Class)
        ):
            error(
                "mapping_target_range_invalid",
                "target.iri",
                "Object property ranges must name a declared destination class.",
            )

    def selector_checks(selector, field):
        if selector.datatype not in DATATYPES:
            error(
                "mapping_datatype_unsupported",
                field + ".datatype",
                "This source datatype is outside the bounded language.",
            )
        if selector.kind == "attribute":
            for member, source_field in zip(sources, source_fields, strict=True):
                actual = attribute_datatype(member.ifc_class, selector.name)
                context = f" Source: {source_field}." if isinstance(source, EntityUnionSelector) else ""
                if actual is None:
                    error(
                        "mapping_attribute_unsupported",
                        field + ".name",
                        "This IFC4 attribute is absent, non-scalar or requires unsupported units." + context,
                    )
                elif actual != selector.datatype:
                    error(
                        "mapping_source_datatype_mismatch",
                        field + ".datatype",
                        "The datatype must match the IFC4 attribute declaration." + context,
                    )
        elif selector.name == "FireRating":
            error(
                "mapping_interpretation_required",
                field + ".name",
                "Fire-resistance mappings remain unresolved and excluded from execution.",
            )
        elif (
            selector.name == "IsExternal"
            and selector.pset
            and selector.pset.startswith("Pset_")
            and selector.datatype != XSD + "boolean"
        ):
            error(
                "mapping_source_datatype_mismatch",
                field + ".datatype",
                "The common IsExternal property is Boolean, never a string or number.",
            )
        if selector.datatype in NUMERIC_DATATYPES and selector.unit is None:
            error(
                "mapping_units_required",
                field + ".unit",
                "Numeric selectors require explicit unitless, ifc_length or ifc_area units.",
            )
        if selector.datatype not in NUMERIC_DATATYPES and selector.unit is not None:
            error("mapping_units_invalid", field + ".unit", "Units cannot be attached to nonnumeric values.")
        if selector.kind == "quantity" and selector.unit not in {"ifc_length", "ifc_area"}:
            error("mapping_units_required", field + ".unit", "Quantity selectors require a measurement unit.")

    condition_count = 0

    def condition_checks(condition, field, depth=1):
        nonlocal condition_count
        condition_count += 1
        if condition_count > 32:
            if condition_count == 33:
                error("mapping_condition_too_large", field, "Conditions are limited to 32 nodes in total.")
            return
        if depth > 4:
            error("mapping_condition_too_deep", field, "Conditions are limited to four levels.")
            return
        if isinstance(condition, CompoundCondition):
            for index, child in enumerate(condition.conditions):
                condition_checks(child, f"{field}.conditions[{index}]", depth + 1)
        else:
            selector_checks(condition.selector, field + ".selector")
            if any(not valid_literal(value, condition.selector.datatype) for value in condition.values):
                error(
                    "mapping_condition_value_invalid",
                    field + ".values",
                    "Condition values must have the selector's exact datatype; no truthy or numeric-string casts.",
                )

    if mapping.condition:
        condition_checks(mapping.condition, "condition")
    if isinstance(target, ClassAssertion) and target.grouping is not None:
        selector_checks(target.grouping, "target.grouping")
        if target.grouping.kind != "property" or target.grouping.datatype != XSD + "string":
            error("mapping_grouping_invalid", "target.grouping", "Grouping requires an exact string property selector.")
    if isinstance(target, DatatypeAssertion):
        if target.datatype not in DATATYPES:
            error(
                "mapping_datatype_unsupported",
                "target.datatype",
                "This target datatype is outside the bounded language.",
            )
        if ranges != {URIRef(target.datatype)}:
            error(
                "mapping_target_datatype_mismatch",
                "target.datatype",
                "The datatype must equal the destination's single declared range.",
            )
        if any((iri, predicate, None) in module.graph for module in modules for predicate in UNIT_PREDICATES):
            error(
                "mapping_units_unsupported",
                "target.iri",
                "Unit-bearing destination properties require an approved unit transformation.",
            )
        value = target.value
        if isinstance(value, ConstantValue):
            if not valid_literal(value.value, target.datatype):
                error(
                    "mapping_literal_invalid",
                    "target.value",
                    "The literal does not have the exact target datatype, or is nonfinite/out of range.",
                )
        else:
            selector_checks(value.selector, "target.value.selector")
            for index, selector in enumerate(value.fallback):
                selector_checks(selector, f"target.value.fallback[{index}]")
            if value.selector.unit in {"ifc_length", "ifc_area"} and target.datatype not in NUMERIC_DATATYPES:
                error("mapping_units_invalid", "target.datatype", "Measurement outputs must have a numeric datatype.")
            if value.transform == "identity" and value.selector.datatype != target.datatype:
                error(
                    "mapping_transform_type_mismatch",
                    "target.value.transform",
                    "Identity requires identical source and target datatypes.",
                )
            if value.transform == "numeric_cast" and not (
                value.selector.datatype in NUMERIC_DATATYPES and target.datatype in NUMERIC_DATATYPES
            ):
                error(
                    "mapping_numeric_cast_invalid",
                    "target.value.transform",
                    "Numeric casts accept only numeric values; strings such as REI60 are never parsed.",
                )
            seen = set()
            for index, entry in enumerate(value.lookup):
                key = (type(entry.input), entry.input)
                if (
                    key in seen
                    or not valid_literal(entry.input, value.selector.datatype)
                    or not valid_literal(entry.output, target.datatype)
                ):
                    error(
                        "mapping_lookup_invalid",
                        f"target.value.lookup[{index}]",
                        "Lookup inputs must be unique and both sides must have their declared datatype.",
                    )
                seen.add(key)
    return (None if diagnostics else mapping), diagnostics


def mapping_conflicts(compiled):
    diagnostics = []
    items = list(compiled.items())
    for index, (mapping_id, mapping) in enumerate(items):
        target = mapping.target
        if not isinstance(target, DatatypeAssertion) or not isinstance(target.value, ConstantValue):
            continue
        for other_id, other in items[:index]:
            other_target = other.target
            if not isinstance(other_target, DatatypeAssertion) or not isinstance(other_target.value, ConstantValue):
                continue
            if target.iri != other_target.iri or "single" not in (target.cardinality, other_target.cardinality):
                continue
            overlap = any(
                (
                    source.ifc_class == other_source.ifc_class
                    or source.match == "subtypes"
                    and is_subtype(other_source.ifc_class, source.ifc_class)
                    or other_source.match == "subtypes"
                    and is_subtype(source.ifc_class, other_source.ifc_class)
                )
                and (
                    not source.predefined_type
                    or not other_source.predefined_type
                    or source.predefined_type == other_source.predefined_type
                )
                for source in source_selectors(mapping.source)
                for other_source in source_selectors(other.source)
            )
            if (
                overlap
                and mapping.condition == other.condition
                and (type(target.value.value), target.value.value)
                != (type(other_target.value.value), other_target.value.value)
            ):
                diagnostics.append(
                    TranslationValidationDiagnostic(
                        code="mapping_output_conflict",
                        severity="error",
                        mapping_id=mapping_id,
                        field_path="contract.mappings",
                        message=f"Single-valued constant output conflicts with {other_id}. "
                        "No first-value-wins policy is allowed.",
                    )
                )
    return diagnostics
