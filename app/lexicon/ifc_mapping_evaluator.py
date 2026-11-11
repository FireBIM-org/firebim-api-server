"""Fixture-only reference evaluation of semantically validated IFC4 mappings.

Native records are JSON-like dictionaries with ``id: str``, ``ifc_class: str``,
optional ``predefined_type: str``, ``attributes: {name: scalar-or-list}``,
``properties: {pset: {name: scalar-or-list}}`` and ``type_properties`` in the
same shape. Quantities use separate ``quantities`` and ``type_quantities`` maps.
Measurements may wrap a numeric ``value`` and a ``unit`` object containing exactly
``symbol``, ``dimension`` (length/area), and positive finite ``scale_to_si``.
Per-value units override model units; absent model area units square model length
units. Otherwise missing units default to SI. Outputs retain their source units;
only comparisons normalize physical values. Relationship records also have
``relating: entity-id`` and ``related: [entity-id]``. Values must already be extracted and typed; None is
absent, never a false Boolean. No IFC/SPF parsing, uploads, TTL or inference.

Every entity identity is ``urn:firebim:model:<model>:entity:<id>`` with each
component independently URL-encoded (including slashes, colons and percent).
Evidence uses the original, model-local source ID, not the encoded identity.

Properties merge per key: an occurrence key overrides its type key even when
its value is None. Lists are multiple values; None items are absent. A bad
selected value invalidates the whole read. Conditions use existential typed
equality/membership over these values, never Boolean/numeric coercion. All
condition branches are checked; invalid or required-missing branches suppress
the mapping, including inside any_of. Optional missing reads simply skip.
Measurement fallbacks select the first present source, never replace invalid
values, and compare every provided source. Conflicting values within one
measurement source fail closed; disagreements between sources are warnings.

String property grouping creates a model/class/name-scoped entity, not a type
assertion on the source IFC entity. Memberships retain source provenance outside
the assertion graph; no ontology membership predicate is invented.

Constants and transform outputs must satisfy the declared datatype. Lookup
requires an explicit typed key; numeric_cast requires numeric datatypes and
rejects truncation, precision loss and overflow. There is no FireRating parser.
Identical typed assertions merge evidence; any applicable single cardinality
suppresses every conflicting output for that subject/predicate across mappings.

Only exact IfcRelAggregates and IfcRelContainedInSpatialStructure sources can
produce object assertions, in either endpoint direction. Aggregates are limited
to Project/Site/Building/BuildingStorey/Space parents and spatial children in
that vocabulary (not Project). Containment requires a spatial structure parent
and IfcElement children. Every endpoint must exist; an invalid relationship
emits no pairs. No transitive or room-membership inference is performed.
Malformed record shapes or duplicate IDs abort the whole batch. Mapping
semantics/bounds are validated upstream, not by this fixture evaluator.
"""

import struct
from dataclasses import dataclass, replace
from urllib.parse import quote

from app.lexicon.ifc4_schema import is_entity, is_subtype
from app.lexicon.ifc_mapping_units import Unit, parse_model_units, physical_key, source_value
from app.lexicon.ifc_mapping_values import NUMERIC_DATATYPES, XSD, valid_literal
from app.schemas.ifc_mapping import (
    Condition,
    DatatypeAssertion,
    EntityUnionSelector,
    ExecutableMapping,
    ValueSelector,
    source_selectors,
)

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RELATIONSHIPS = frozenset({"IfcRelAggregates", "IfcRelContainedInSpatialStructure"})
SPATIAL_CLASSES = ("IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcSpace")


@dataclass(frozen=True)
class Evidence:
    mapping_id: str
    source_id: str
    kind: str = "entity"
    name: str | None = None
    pset: str | None = None
    origin: str | None = None
    relating: str | None = None
    related: str | None = None
    unit_origin: str | None = None


@dataclass(frozen=True)
class Assertion:
    subject: str
    predicate: str
    object: str | bool | int | float
    datatype: str | None = None
    evidence: tuple[Evidence, ...] = ()
    unit: Unit | None = None


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    mapping_id: str | None = None
    source_id: str | None = None
    severity: str = "error"


@dataclass(frozen=True)
class GroupMembership:
    group: str
    member: str
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class EvaluationResult:
    assertions: tuple[Assertion, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    memberships: tuple[GroupMembership, ...] = ()


@dataclass(frozen=True)
class _SourceValue:
    value: str | bool | int | float
    unit: Unit | None
    evidence: Evidence


def _identity(model_id: str, record_id: str) -> str:
    return f"urn:firebim:model:{quote(model_id, safe='')}:entity:{quote(record_id, safe='')}"


def _group_identity(model_id: str, target_iri: str, name: str) -> str:
    return f"urn:firebim:model:{quote(model_id, safe='')}:group:{quote(target_iri, safe='')}:{quote(name, safe='')}"


def _index_records(records: list[dict]) -> tuple[dict[str, dict], list[Diagnostic]]:
    index = {}
    seen = set()
    diagnostics = []
    for record in records:
        identifier = record.get("id") if isinstance(record, dict) else None
        if not isinstance(identifier, str) or not identifier.strip():
            diagnostics.append(Diagnostic("invalid_record_id", "Every fixture record needs a nonempty string ID"))
            continue
        if identifier in seen:
            diagnostics.append(
                Diagnostic(
                    "duplicate_record_id",
                    "Duplicate model-local record ID",
                    source_id=identifier,
                )
            )
        seen.add(identifier)
        actual = record.get("ifc_class")
        containers_valid = isinstance(record.get("attributes", {}), dict) and all(
            isinstance(record.get(key, {}), dict)
            and all(isinstance(pset, dict) for pset in record.get(key, {}).values())
            for key in ("properties", "type_properties", "quantities", "type_quantities")
        )
        if (
            not isinstance(actual, str)
            or not is_entity(actual)
            or not containers_valid
            or (record.get("predefined_type") is not None and not isinstance(record["predefined_type"], str))
        ):
            diagnostics.append(
                Diagnostic(
                    "invalid_record",
                    "Invalid IFC class or fixture record shape",
                    source_id=identifier,
                )
            )
            continue
        index[identifier] = record
    return index, sorted(set(diagnostics), key=repr)


def _matches(mapping: ExecutableMapping, record: dict) -> bool:
    actual = record["ifc_class"]
    return any(
        (actual == source.ifc_class or source.match == "subtypes" and is_subtype(actual, source.ifc_class))
        and (source.predefined_type is None or record.get("predefined_type") == source.predefined_type)
        for source in source_selectors(mapping.source)
    )


def _read(
    selector: ValueSelector,
    record: dict,
    mapping_id: str,
    diagnostics: list[Diagnostic],
    model_units: dict[str, Unit],
    *,
    check_required: bool = True,
) -> tuple[list[_SourceValue], Evidence] | None:
    origin = "occurrence"
    if selector.kind == "attribute":
        value = record.get("attributes", {}).get(selector.name)
    else:
        container = "quantities" if selector.kind == "quantity" else "properties"
        occurrence = record.get(container, {}).get(selector.pset, {})
        inherited = record.get("type_" + container, {}).get(selector.pset, {})
        if selector.name in occurrence:
            value = occurrence[selector.name]
        else:
            value = inherited.get(selector.name)
            origin = "type"
    evidence = Evidence(mapping_id, record["id"], selector.kind, selector.name, selector.pset, origin)
    values = value if isinstance(value, list) else [value]
    values = [item for item in values if item is not None]
    if not values:
        if selector.required and check_required:
            diagnostics.append(
                Diagnostic(
                    "missing_required",
                    f"Missing {selector.pset or 'attribute'}.{selector.name}",
                    mapping_id,
                    record["id"],
                )
            )
            return None
        return [], evidence
    try:
        selected = []
        for item in values:
            value, unit, unit_origin = source_value(item, selector.datatype, selector.unit, model_units)
            selected.append(_SourceValue(value, unit, replace(evidence, unit_origin=unit_origin)))
    except ValueError:
        diagnostics.append(
            Diagnostic(
                "invalid_source_value",
                f"Invalid value for {selector.pset or 'attribute'}.{selector.name}",
                mapping_id,
                record["id"],
            )
        )
        return None
    if (
        selector.unit in {"ifc_length", "ifc_area"}
        and len({physical_key(item.value, item.unit) for item in selected}) > 1
    ):
        diagnostics.append(
            Diagnostic(
                "cardinality_conflict",
                f"Conflicting measurement values for {selector.pset}.{selector.name}",
                mapping_id,
                record["id"],
            )
        )
        return None
    return selected, evidence


def _typed_equal(left, right) -> bool:
    return type(left) is type(right) and left == right


def _condition(
    condition: Condition,
    record: dict,
    mapping_id: str,
    diagnostics: list[Diagnostic],
    model_units: dict[str, Unit],
) -> tuple[bool | None, tuple[Evidence, ...]]:
    if condition.kind in ("all_of", "any_of"):
        outcomes = [_condition(child, record, mapping_id, diagnostics, model_units) for child in condition.conditions]
        evidence = tuple(item for _, items in outcomes for item in items)
        if any(outcome is None for outcome, _ in outcomes):
            return None, evidence
        combine = all if condition.kind == "all_of" else any
        return combine(outcome for outcome, _ in outcomes), evidence
    selected = _read(condition.selector, record, mapping_id, diagnostics, model_units)
    if selected is None:
        return None, ()
    values, evidence = selected
    compared_evidence = tuple(item.evidence for item in values) or (evidence,)
    if condition.kind == "present":
        return bool(values), compared_evidence
    return any(
        _typed_equal(value.value, expected) for value in values for expected in condition.values
    ), compared_evidence


def _numeric_cast(value, source_datatype: str, target_datatype: str):
    if source_datatype not in NUMERIC_DATATYPES or target_datatype not in NUMERIC_DATATYPES:
        raise ValueError("Numeric casts require numeric input and output datatypes")
    if target_datatype == XSD + "integer":
        converted = int(value)
    elif target_datatype == XSD + "float":
        converted = struct.unpack("!f", struct.pack("!f", float(value)))[0]
    elif target_datatype == XSD + "double":
        converted = float(value)
    else:
        converted = value
    if converted != value or not valid_literal(converted, target_datatype):
        raise ValueError("Numeric cast would truncate, lose precision or overflow")
    return converted


def _outputs(
    target: DatatypeAssertion,
    record: dict,
    mapping_id: str,
    diagnostics: list[Diagnostic],
    model_units: dict[str, Unit],
) -> list[tuple[str | bool | int | float, Unit | None, tuple[Evidence, ...]]]:
    if target.value.kind == "constant":
        evidence = (Evidence(mapping_id, record["id"], "constant"),)
        values = [_SourceValue(target.value.value, None, evidence[0])]
    else:
        selectors = [target.value.selector, *target.value.fallback]
        selected = [
            _read(selector, record, mapping_id, diagnostics, model_units, check_required=not target.value.fallback)
            for selector in selectors
        ]
        if any(item is None for item in selected):
            return []
        provided = [item[0] for item in selected if item[0]]
        if not provided:
            if target.value.fallback and any(selector.required for selector in selectors):
                diagnostics.append(
                    Diagnostic(
                        "missing_required",
                        "No measurement source is present",
                        mapping_id,
                        record["id"],
                    )
                )
            return []
        values = provided[0]
        evidence = tuple(item.evidence for source in provided for item in source)
        if target.value.fallback and any(
            physical_key(source[0].value, source[0].unit) != physical_key(values[0].value, values[0].unit)
            for source in provided[1:]
        ):
            diagnostics.append(
                Diagnostic(
                    "mapping_source_disagreement",
                    "Measurement sources disagree; the first present source is preferred",
                    mapping_id,
                    record["id"],
                    "warning",
                )
            )
    outputs = []
    for item in values:
        value = item.value
        if target.value.kind == "read":
            if target.value.transform == "numeric_cast":
                try:
                    value = _numeric_cast(value, target.value.selector.datatype, target.datatype)
                except (ValueError, OverflowError):
                    diagnostics.append(
                        Diagnostic(
                            "invalid_numeric_cast",
                            "Numeric cast requires lossless, finite numeric values",
                            mapping_id,
                            record["id"],
                        )
                    )
                    continue
            elif target.value.transform == "lookup":
                entries = [entry for entry in target.value.lookup if _typed_equal(entry.input, value)]
                if len(entries) != 1:
                    diagnostics.append(
                        Diagnostic(
                            "lookup_miss" if not entries else "ambiguous_lookup",
                            "Lookup requires exactly one typed input match",
                            mapping_id,
                            record["id"],
                        )
                    )
                    continue
                value = entries[0].output
        if not valid_literal(value, target.datatype):
            diagnostics.append(
                Diagnostic(
                    "invalid_output_value",
                    "Value does not match target datatype",
                    mapping_id,
                    record["id"],
                )
            )
            continue
        if item.unit is not None and target.datatype not in NUMERIC_DATATYPES:
            diagnostics.append(
                Diagnostic(
                    "invalid_output_value",
                    "Measurement output must have a numeric datatype",
                    mapping_id,
                    record["id"],
                )
            )
            continue
        outputs.append((value, item.unit, evidence))
    return outputs


def _is_a(record: dict, ifc_class: str) -> bool:
    return record["ifc_class"] == ifc_class or is_subtype(record["ifc_class"], ifc_class)


def _relationship_pairs(
    record: dict,
    index: dict[str, dict],
    mapping_id: str,
    diagnostics: list[Diagnostic],
) -> list[tuple[str, str]]:
    relating = record.get("relating")
    related = record.get("related")
    if (
        not isinstance(relating, str)
        or not relating
        or not isinstance(related, list)
        or not related
        or any(not isinstance(item, str) or not item for item in related)
    ):
        diagnostics.append(
            Diagnostic(
                "invalid_relationship",
                "Relationship needs one relating ID and a nonempty related ID list",
                mapping_id,
                record["id"],
            )
        )
        return []
    if relating not in index or any(item not in index for item in related):
        diagnostics.append(
            Diagnostic(
                "missing_relationship_endpoint",
                "Relationship references an absent entity",
                mapping_id,
                record["id"],
            )
        )
        return []
    parent = index[relating]
    if record["ifc_class"] == "IfcRelAggregates":
        parent_valid = _is_a(parent, "IfcProject") or any(_is_a(parent, cls) for cls in SPATIAL_CLASSES)
        children_valid = all(any(_is_a(index[item], cls) for cls in SPATIAL_CLASSES) for item in related)
    else:
        parent_valid = _is_a(parent, "IfcSpatialStructureElement")
        children_valid = all(_is_a(index[item], "IfcElement") for item in related)
    if not parent_valid or not children_valid or relating in related:
        diagnostics.append(
            Diagnostic(
                "invalid_relationship_endpoint",
                "Relationship endpoints do not have the permitted IFC spatial roles",
                mapping_id,
                record["id"],
            )
        )
        return []
    return [(relating, item) for item in sorted(set(related))]


def _finish(
    assertions: list[Assertion],
    singles: set[tuple[str, str]],
    diagnostics: list[Diagnostic],
    memberships: list[GroupMembership],
) -> EvaluationResult:
    groups = {}
    for assertion in assertions:
        key = assertion.subject, assertion.predicate
        value_key = assertion.datatype, physical_key(assertion.object, assertion.unit)
        values = groups.setdefault(key, {})
        values.setdefault(value_key, []).append(assertion)
    merged = []
    for (subject, predicate), values in sorted(groups.items()):
        if (subject, predicate) in singles and len(values) > 1:
            diagnostics.append(
                Diagnostic(
                    "cardinality_conflict",
                    f"Conflicting single values for {subject} {predicate}",
                )
            )
            continue
        for _, candidates in sorted(values.items(), key=lambda item: repr(item[0])):
            evidence = {item for candidate in candidates for item in candidate.evidence}
            merged.append(replace(candidates[0], evidence=tuple(sorted(evidence, key=repr))))
    member_groups = {}
    for membership in memberships:
        member_groups.setdefault((membership.group, membership.member), set()).update(membership.evidence)
    merged_memberships = tuple(
        GroupMembership(group, member, tuple(sorted(evidence, key=repr)))
        for (group, member), evidence in sorted(member_groups.items())
    )
    return EvaluationResult(tuple(merged), tuple(dict.fromkeys(diagnostics)), merged_memberships)


def evaluate_ifc_mappings(
    mappings: dict[str, ExecutableMapping],
    records: list[dict],
    model_id: str,
    *,
    model_units: dict | None = None,
) -> EvaluationResult:
    """Evaluate extracted fixtures only; mapping semantic validation is upstream."""
    if not isinstance(model_id, str) or not model_id.strip():
        return EvaluationResult(diagnostics=(Diagnostic("invalid_model_id", "A nonempty model ID is required"),))
    try:
        units = parse_model_units(model_units)
    except ValueError as exc:
        return EvaluationResult(diagnostics=(Diagnostic("invalid_model_units", str(exc)),))
    index, diagnostics = _index_records(records)
    if diagnostics:
        return EvaluationResult(diagnostics=tuple(diagnostics))
    assertions = []
    memberships = []
    singles = set()
    for mapping_id, mapping in sorted(mappings.items()):
        if mapping.target.kind == "object_property" and (
            isinstance(mapping.source, EntityUnionSelector)
            or mapping.source.ifc_class not in RELATIONSHIPS
            or mapping.source.match != "exact"
            or {mapping.subject, mapping.target.object} != {"relating", "related"}
        ):
            diagnostics.append(
                Diagnostic(
                    "unsupported_relationship_mapping",
                    "Object assertions require exact supported relationships and opposite roles",
                    mapping_id,
                )
            )
            continue
        if mapping.target.kind != "object_property" and mapping.subject != "entity":
            diagnostics.append(
                Diagnostic(
                    "unsupported_subject",
                    "Class and datatype assertions require an entity subject",
                    mapping_id,
                )
            )
            continue
        for record in sorted(index.values(), key=lambda item: item["id"]):
            if not _matches(mapping, record):
                continue
            condition_evidence = ()
            if mapping.condition is not None:
                matches, condition_evidence = _condition(mapping.condition, record, mapping_id, diagnostics, units)
                if matches is not True:
                    continue
            if mapping.target.kind == "class":
                subject = _identity(model_id, record["id"])
                evidence = (Evidence(mapping_id, record["id"]),) + condition_evidence
                if mapping.target.grouping is not None:
                    if mapping.target.grouping.kind != "property" or mapping.target.grouping.datatype != XSD + "string":
                        diagnostics.append(
                            Diagnostic(
                                "invalid_grouping_selector",
                                "Grouping requires a string property selector",
                                mapping_id,
                                record["id"],
                            )
                        )
                        continue
                    selected = _read(mapping.target.grouping, record, mapping_id, diagnostics, units)
                    if selected is None:
                        continue
                    values, _ = selected
                    if not values:
                        continue
                    names = {item.value for item in values}
                    if len(names) != 1:
                        diagnostics.append(
                            Diagnostic(
                                "cardinality_conflict",
                                "Conflicting grouping names",
                                mapping_id,
                                record["id"],
                            )
                        )
                        continue
                    name = values[0].value
                    if not name.strip():
                        continue
                    evidence = tuple(item.evidence for item in values) + condition_evidence
                    subject = _group_identity(model_id, mapping.target.iri, name)
                    memberships.append(GroupMembership(subject, _identity(model_id, record["id"]), evidence))
                assertions.append(
                    Assertion(
                        subject,
                        RDF_TYPE,
                        mapping.target.iri,
                        evidence=evidence,
                    )
                )
            elif mapping.target.kind == "datatype_property":
                if mapping.target.cardinality == "single":
                    singles.add((_identity(model_id, record["id"]), mapping.target.iri))
                for value, unit, evidence in _outputs(mapping.target, record, mapping_id, diagnostics, units):
                    assertions.append(
                        Assertion(
                            _identity(model_id, record["id"]),
                            mapping.target.iri,
                            value,
                            mapping.target.datatype,
                            evidence + condition_evidence,
                            unit,
                        )
                    )
            elif mapping.target.kind == "object_property":
                for relating, related in _relationship_pairs(record, index, mapping_id, diagnostics):
                    subject = _identity(model_id, relating if mapping.subject == "relating" else related)
                    value = _identity(model_id, relating if mapping.target.object == "relating" else related)
                    evidence = Evidence(mapping_id, record["id"], "relationship", relating=relating, related=related)
                    if mapping.target.cardinality == "single":
                        singles.add((subject, mapping.target.iri))
                    assertions.append(
                        Assertion(
                            subject,
                            mapping.target.iri,
                            value,
                            evidence=(evidence,) + condition_evidence,
                        )
                    )
    return _finish(assertions, singles, diagnostics, memberships)
