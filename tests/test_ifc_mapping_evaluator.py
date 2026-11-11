import pytest

from app.lexicon.ifc_mapping_evaluator import evaluate_ifc_mappings
from app.schemas.ifc_mapping import ExecutableMapping

XSD = "http://www.w3.org/2001/XMLSchema#"
EX = "https://example.org/"
POLICIES = {
    "identity": "model_scoped",
    "property_precedence": "occurrence_over_type",
    "missing": "skip",
    "invalid": "error",
    "conflicts": "error",
}


def mapping(target=None, *, ifc_class="IfcWall", match="exact", predefined_type=None, subject="entity", condition=None):
    return ExecutableMapping.model_validate(
        {
            "language": "ifc4-mapping/1",
            "source": {"schema": "IFC4", "ifc_class": ifc_class, "match": match, "predefined_type": predefined_type},
            "subject": subject,
            "target": target or {"kind": "class", "iri": EX + "Wall"},
            "condition": condition,
            "policies": POLICIES,
        }
    )


def record(identifier="w1", ifc_class="IfcWall", **fields):
    return {
        "id": identifier,
        "ifc_class": ifc_class,
        "attributes": {},
        "properties": {},
        "type_properties": {},
        **fields,
    }


def test_class_selection_and_model_scoped_identity():
    records = [record("wall /:α"), record("standard", "IfcWallStandardCase"), record("door", "IfcDoor")]
    exact = evaluate_ifc_mappings({"class": mapping()}, records, "model /:α")
    assert not exact.diagnostics
    assert len(exact.assertions) == 1
    assertion = exact.assertions[0]
    assert assertion.subject == "urn:firebim:model:model%20%2F%3A%CE%B1:entity:wall%20%2F%3A%CE%B1"
    assert assertion.predicate == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    assert assertion.object == EX + "Wall"
    assert assertion.datatype is None
    assert assertion.evidence[0].source_id == "wall /:α"
    subtypes = evaluate_ifc_mappings({"class": mapping(match="subtypes")}, records, "other")
    assert len(subtypes.assertions) == 2
    assert assertion.subject not in {item.subject for item in subtypes.assertions}
    selected = evaluate_ifc_mappings(
        {"class": mapping(predefined_type="STANDARD")},
        [record("yes", predefined_type="STANDARD"), record("no", predefined_type="USERDEFINED"), record()],
        "m",
    )
    assert [item.subject for item in selected.assertions] == ["urn:firebim:model:m:entity:yes"]


def selector(name="IsExternal", datatype="boolean", *, required=False, kind="property", pset="Pset_WallCommon"):
    result = {"kind": kind, "name": name, "datatype": XSD + datatype, "required": required}
    if kind == "property":
        result["pset"] = pset
    return result


def read_target(selected=None, *, datatype="boolean", transform="identity", lookup=None, cardinality="single"):
    value = {"kind": "read", "selector": selected or selector(), "transform": transform}
    if lookup is not None:
        value["lookup"] = lookup
    return {
        "kind": "datatype_property",
        "iri": EX + "value",
        "datatype": XSD + datatype,
        "value": value,
        "cardinality": cardinality,
    }


def test_property_precedence_is_per_key_and_null_masks_inheritance():
    result = evaluate_ifc_mappings(
        {"read": mapping(read_target(selector(required=True)))},
        [
            record(
                "inherited",
                properties={"Pset_WallCommon": {"Other": "local"}},
                type_properties={"Pset_WallCommon": {"IsExternal": True}},
            ),
            record(
                "overridden",
                properties={"Pset_WallCommon": {"IsExternal": False}},
                type_properties={"Pset_WallCommon": {"IsExternal": True}},
            ),
            record(
                "masked",
                properties={"Pset_WallCommon": {"IsExternal": None}},
                type_properties={"Pset_WallCommon": {"IsExternal": True}},
            ),
            record("missing"),
            record("invalid", properties={"Pset_WallCommon": {"IsExternal": "false"}}),
        ],
        "m",
    )
    assert [(item.subject, item.object, item.datatype) for item in result.assertions] == [
        ("urn:firebim:model:m:entity:inherited", True, XSD + "boolean"),
        ("urn:firebim:model:m:entity:overridden", False, XSD + "boolean"),
    ]
    assert [(item.code, item.source_id) for item in result.diagnostics] == [
        ("invalid_source_value", "invalid"),
        ("missing_required", "masked"),
        ("missing_required", "missing"),
    ]
    inherited, overridden = [item.evidence[0] for item in result.assertions]
    assert (inherited.source_id, inherited.kind, inherited.pset, inherited.name, inherited.origin) == (
        "inherited",
        "property",
        "Pset_WallCommon",
        "IsExternal",
        "type",
    )
    assert overridden.origin == "occurrence"


def test_conditions_are_typed_nested_and_do_not_treat_invalid_as_present():
    integer = selector("Count", "integer", kind="attribute")
    condition = {
        "kind": "all_of",
        "conditions": [
            {"kind": "present", "selector": integer},
            {
                "kind": "any_of",
                "conditions": [
                    {"kind": "equals", "selector": integer, "values": [1]},
                    {"kind": "in", "selector": integer, "values": [3, 5]},
                ],
            },
        ],
    }
    result = evaluate_ifc_mappings(
        {"condition": mapping(condition=condition)},
        [
            record("one", attributes={"Count": 1}),
            record("three", attributes={"Count": [3, 7]}),
            record("two", attributes={"Count": 2}),
            record("bool", attributes={"Count": True}),
            record("missing"),
            record("null", attributes={"Count": None}),
        ],
        "m",
    )
    assert [item.subject for item in result.assertions] == [
        "urn:firebim:model:m:entity:one",
        "urn:firebim:model:m:entity:three",
    ]
    assert {item.source_id for item in result.diagnostics} == {"bool"}
    assert {item.code for item in result.diagnostics} == {"invalid_source_value"}
    assert {(item.kind, item.name, item.origin) for item in result.assertions[0].evidence} == {
        ("entity", None, None),
        ("attribute", "Count", "occurrence"),
    }
    for operator, values in [("equals", [1]), ("in", [1, False])]:
        typed = evaluate_ifc_mappings(
            {
                "typed": mapping(
                    condition={
                        "kind": operator,
                        "selector": selector(),
                        "values": values,
                    }
                )
            },
            [record(properties={"Pset_WallCommon": {"IsExternal": True}})],
            "m",
        )
        assert not typed.assertions  # True must not compare equal to 1.


def test_invalid_condition_branch_fails_closed_even_when_another_branch_matches():
    condition = {
        "kind": "any_of",
        "conditions": [
            {"kind": "present", "selector": selector("Name", "string", kind="attribute")},
            {"kind": "present", "selector": selector(required=True)},
        ],
    }
    invalid = evaluate_ifc_mappings(
        {"condition": mapping(condition=condition)},
        [
            record(attributes={"Name": "Wall"}, properties={"Pset_WallCommon": {"IsExternal": "false"}}),
        ],
        "m",
    )
    assert not invalid.assertions
    assert [item.code for item in invalid.diagnostics] == ["invalid_source_value"]
    absent = evaluate_ifc_mappings(
        {
            "condition": mapping(
                condition={
                    "kind": "present",
                    "selector": selector(required=True),
                }
            )
        },
        [record()],
        "m",
    )
    assert not absent.assertions
    assert [item.code for item in absent.diagnostics] == ["missing_required"]


def test_missing_required_condition_prevents_assertion_even_in_matching_any_group():
    result = evaluate_ifc_mappings(
        {
            "required": mapping(
                condition={
                    "kind": "any_of",
                    "conditions": [
                        {"kind": "present", "selector": selector("Name", "string", kind="attribute")},
                        {"kind": "present", "selector": selector(required=True)},
                    ],
                }
            )
        },
        [record(attributes={"Name": "Wall"})],
        "m",
    )
    assert not result.assertions
    assert [item.code for item in result.diagnostics] == ["missing_required"]


def constant_target(value, *, datatype="boolean", cardinality="single"):
    return {
        "kind": "datatype_property",
        "iri": EX + "value",
        "datatype": XSD + datatype,
        "value": {"kind": "constant", "value": value},
        "cardinality": cardinality,
    }


@pytest.mark.parametrize(
    ("value", "datatype", "expected", "code"),
    [
        (False, "boolean", False, None),
        ("REI60", "string", "REI60", None),
        (60, "integer", 60, None),
        ("false", "boolean", None, "invalid_output_value"),
        (True, "integer", None, "invalid_output_value"),
        (1e39, "float", None, "invalid_output_value"),
    ],
)
def test_constants_require_exact_output_datatype(value, datatype, expected, code):
    result = evaluate_ifc_mappings({"constant": mapping(constant_target(value, datatype=datatype))}, [record()], "m")
    if code:
        assert not result.assertions
        assert [item.code for item in result.diagnostics] == [code]
    else:
        assert not result.diagnostics
        assert len(result.assertions) == 1
        assert result.assertions[0].object == expected
        assert type(result.assertions[0].object) is type(expected)
        assert result.assertions[0].datatype == XSD + datatype


def test_identical_assertions_are_deduplicated_with_all_mapping_evidence():
    mappings = {
        "read": mapping(read_target(cardinality="many")),
        "constant": mapping(constant_target(False)),
        "class_b": mapping(),
        "class_a": mapping(),
    }
    records = [record(properties={"Pset_WallCommon": {"IsExternal": [False, False]}})]
    result = evaluate_ifc_mappings(mappings, records, "m")
    assert not result.diagnostics
    assert len(result.assertions) == 2
    literal = next(item for item in result.assertions if item.datatype)
    assert literal.object is False
    assert {item.mapping_id for item in literal.evidence} == {"read", "constant"}
    assert len(literal.evidence) == 2
    classification = next(item for item in result.assertions if item.datatype is None)
    assert {item.mapping_id for item in classification.evidence} == {"class_a", "class_b"}
    assert result == evaluate_ifc_mappings(dict(reversed(list(mappings.items()))), records, "m")


@pytest.mark.parametrize(
    ("left_cardinality", "right_cardinality", "conflict"),
    [
        ("single", "single", True),
        ("single", "many", True),
        ("many", "single", True),
        ("many", "many", False),
    ],
)
def test_cardinality_conflicts_across_mappings_omit_all_not_first_wins(left_cardinality, right_cardinality, conflict):
    result = evaluate_ifc_mappings(
        {
            "left": mapping(constant_target(False, cardinality=left_cardinality)),
            "right": mapping(constant_target(True, cardinality=right_cardinality)),
            "unaffected": mapping(),
        },
        [record()],
        "m",
    )
    literals = [item for item in result.assertions if item.datatype]
    if conflict:
        assert not literals
        assert [item.code for item in result.diagnostics] == ["cardinality_conflict"]
        assert result.diagnostics[0].severity == "error"
    else:
        assert {item.object for item in literals} == {True, False}
        assert not result.diagnostics
    assert len([item for item in result.assertions if item.datatype is None]) == 1


def test_cardinality_conflict_within_list_and_distinct_typed_literals():
    result = evaluate_ifc_mappings(
        {"read": mapping(read_target())},
        [
            record(properties={"Pset_WallCommon": {"IsExternal": [True, False]}}),
        ],
        "m",
    )
    assert not result.assertions
    assert [item.code for item in result.diagnostics] == ["cardinality_conflict"]
    typed = evaluate_ifc_mappings(
        {
            "boolean": mapping(constant_target(True, cardinality="many")),
            "integer": mapping(constant_target(1, datatype="integer", cardinality="many")),
        },
        [record()],
        "m",
    )
    assert not typed.diagnostics
    assert len(typed.assertions) == 2
    assert {item.datatype for item in typed.assertions} == {XSD + "boolean", XSD + "integer"}


def test_lookup_matches_typed_keys_and_never_parses_fire_rating():
    target = read_target(
        selector("FireRating", "string"),
        datatype="integer",
        transform="lookup",
        lookup=[
            {"input": "REI60", "output": 60},
        ],
    )
    result = evaluate_ifc_mappings(
        {"lookup": mapping(target)},
        [
            record("explicit", properties={"Pset_WallCommon": {"FireRating": "REI60"}}),
            record("no_entry", properties={"Pset_WallCommon": {"FireRating": "REI90"}}),
        ],
        "m",
    )
    assert [(item.subject, item.object) for item in result.assertions] == [
        ("urn:firebim:model:m:entity:explicit", 60),
    ]
    assert [(item.code, item.source_id) for item in result.diagnostics] == [("lookup_miss", "no_entry")]
    typed = evaluate_ifc_mappings(
        {
            "lookup": mapping(
                read_target(
                    transform="lookup",
                    lookup=[
                        {"input": 1, "output": False},
                    ],
                )
            )
        },
        [record(properties={"Pset_WallCommon": {"IsExternal": True}})],
        "m",
    )
    assert not typed.assertions
    assert [item.code for item in typed.diagnostics] == ["lookup_miss"]
    invalid_output = evaluate_ifc_mappings(
        {
            "lookup": mapping(
                read_target(
                    transform="lookup",
                    lookup=[
                        {"input": True, "output": "false"},
                    ],
                )
            )
        },
        [record(properties={"Pset_WallCommon": {"IsExternal": True}})],
        "m",
    )
    assert not invalid_output.assertions
    assert [item.code for item in invalid_output.diagnostics] == ["invalid_output_value"]


@pytest.mark.parametrize(
    ("source_type", "target_type", "value", "expected", "code"),
    [
        ("decimal", "integer", 60.0, 60, None),
        ("integer", "double", 60, 60.0, None),
        ("integer", "decimal", 60, 60, None),
        ("double", "integer", 60.5, None, "invalid_numeric_cast"),
        ("integer", "float", 10**40, None, "invalid_numeric_cast"),
        ("integer", "float", 2**24, float(2**24), None),
        ("integer", "float", 2**24 + 1, None, "invalid_numeric_cast"),
        ("double", "float", 0.1, None, "invalid_numeric_cast"),
        ("double", "float", 1.5, 1.5, None),
        ("integer", "double", 10**400, None, "invalid_numeric_cast"),
        ("integer", "double", 2**53 + 1, None, "invalid_numeric_cast"),
        ("double", "integer", float("inf"), None, "invalid_source_value"),
        ("double", "integer", float("nan"), None, "invalid_source_value"),
        ("integer", "double", True, None, "invalid_source_value"),
        ("integer", "double", "60", None, "invalid_source_value"),
        ("decimal", "integer", "REI60", None, "invalid_source_value"),
        ("string", "integer", "REI60", None, "invalid_numeric_cast"),
        ("boolean", "integer", True, None, "invalid_numeric_cast"),
        ("integer", "string", 60, None, "invalid_numeric_cast"),
    ],
)
def test_numeric_cast_is_numeric_only_without_truncation_or_overflow(source_type, target_type, value, expected, code):
    result = evaluate_ifc_mappings(
        {
            "cast": mapping(
                read_target(
                    selector("Rating", source_type, kind="attribute"),
                    datatype=target_type,
                    transform="numeric_cast",
                )
            )
        },
        [record(attributes={"Rating": value})],
        "m",
    )
    if code:
        assert not result.assertions
        assert [item.code for item in result.diagnostics] == [code]
    else:
        assert not result.diagnostics
        assert len(result.assertions) == 1
        assert result.assertions[0].object == expected
        assert type(result.assertions[0].object) is type(expected)


def relation_mapping(ifc_class="IfcRelAggregates", *, reverse=False, cardinality="many", match="exact"):
    return mapping(
        {
            "kind": "object_property",
            "iri": EX + "partOf" if reverse else EX + "hasPart",
            "object": "relating" if reverse else "related",
            "cardinality": cardinality,
        },
        ifc_class=ifc_class,
        match=match,
        subject="related" if reverse else "relating",
    )


def test_spatial_aggregates_and_direct_containment_only_with_endpoint_evidence():
    records = [
        record("project", "IfcProject"),
        record("building", "IfcBuilding"),
        record("storey", "IfcBuildingStorey"),
        record("space", "IfcSpace"),
        record("wall", "IfcWall"),
        record("door", "IfcDoor"),
        record("project_building", "IfcRelAggregates", relating="project", related=["building"]),
        record("building_storey", "IfcRelAggregates", relating="building", related=["storey"]),
        record("storey_space", "IfcRelAggregates", relating="storey", related=["space"]),
        record("contained", "IfcRelContainedInSpatialStructure", relating="storey", related=["wall", "door"]),
    ]
    mappings = {
        "aggregates": relation_mapping(),
        "containment": relation_mapping("IfcRelContainedInSpatialStructure"),
        "reverse": relation_mapping("IfcRelContainedInSpatialStructure", reverse=True),
    }
    result = evaluate_ifc_mappings(mappings, records, "m")
    assert not result.diagnostics
    assert {(item.subject, item.predicate, item.object) for item in result.assertions} == {
        ("urn:firebim:model:m:entity:project", EX + "hasPart", "urn:firebim:model:m:entity:building"),
        ("urn:firebim:model:m:entity:building", EX + "hasPart", "urn:firebim:model:m:entity:storey"),
        ("urn:firebim:model:m:entity:storey", EX + "hasPart", "urn:firebim:model:m:entity:space"),
        ("urn:firebim:model:m:entity:storey", EX + "hasPart", "urn:firebim:model:m:entity:wall"),
        ("urn:firebim:model:m:entity:storey", EX + "hasPart", "urn:firebim:model:m:entity:door"),
        ("urn:firebim:model:m:entity:wall", EX + "partOf", "urn:firebim:model:m:entity:storey"),
        ("urn:firebim:model:m:entity:door", EX + "partOf", "urn:firebim:model:m:entity:storey"),
    }
    wall = next(item for item in result.assertions if item.object == "urn:firebim:model:m:entity:wall")
    assert wall.datatype is None
    assert len(wall.evidence) == 1
    assert (wall.evidence[0].source_id, wall.evidence[0].kind, wall.evidence[0].relating, wall.evidence[0].related) == (
        "contained",
        "relationship",
        "storey",
        "wall",
    )
    assert not any(item.subject == "urn:firebim:model:m:entity:space" for item in result.assertions)
    assert result == evaluate_ifc_mappings(dict(reversed(list(mappings.items()))), list(reversed(records)), "m")


@pytest.mark.parametrize(
    ("relation", "parent_class", "child_class"),
    [
        ("IfcRelAggregates", "IfcElementAssembly", "IfcWall"),
        ("IfcRelAggregates", "IfcBuildingStorey", "IfcWall"),
        ("IfcRelAggregates", "IfcWall", "IfcSpace"),
        ("IfcRelAggregates", "IfcSite", "IfcProject"),
        ("IfcRelContainedInSpatialStructure", "IfcProject", "IfcWall"),
        ("IfcRelContainedInSpatialStructure", "IfcSpatialZone", "IfcWall"),
        ("IfcRelContainedInSpatialStructure", "IfcWall", "IfcDoor"),
        ("IfcRelContainedInSpatialStructure", "IfcBuildingStorey", "IfcSpace"),
    ],
)
def test_relationship_endpoint_classes_are_checked(relation, parent_class, child_class):
    result = evaluate_ifc_mappings(
        {"relation": relation_mapping(relation)},
        [
            record("parent", parent_class),
            record("child", child_class),
            record("relation", relation, relating="parent", related=["child"]),
        ],
        "m",
    )
    assert not result.assertions
    assert [item.code for item in result.diagnostics] == ["invalid_relationship_endpoint"]
    assert result.diagnostics[0].source_id == "relation"


def test_mixed_containment_with_spatial_zone_rejects_the_whole_relationship():
    result = evaluate_ifc_mappings(
        {"contains": relation_mapping("IfcRelContainedInSpatialStructure")},
        [
            record("building", "IfcBuilding"),
            record("roof", "IfcRoof"),
            record("zone", "IfcSpatialZone"),
            record("proxy", "IfcBuildingElementProxy"),
            record(
                "relation", "IfcRelContainedInSpatialStructure", relating="building", related=["roof", "zone", "proxy"]
            ),
        ],
        "m",
    )
    assert not result.assertions
    assert [(item.code, item.mapping_id, item.source_id) for item in result.diagnostics] == [
        ("invalid_relationship_endpoint", "contains", "relation"),
    ]


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"relating": "parent"},
        {"related": ["child"]},
        {"relating": "parent", "related": []},
        {"relating": ["parent"], "related": ["child"]},
        {"relating": "parent", "related": "child"},
        {"relating": "missing", "related": ["child"]},
        {"relating": "parent", "related": ["child", "missing"]},
    ],
)
def test_malformed_or_dangling_relationship_fails_closed(fields):
    result = evaluate_ifc_mappings(
        {"relation": relation_mapping()},
        [
            record("parent", "IfcBuildingStorey"),
            record("child", "IfcSpace"),
            record("relation", "IfcRelAggregates", **fields),
        ],
        "m",
    )
    assert not result.assertions
    assert result.diagnostics
    assert {item.source_id for item in result.diagnostics} == {"relation"}


@pytest.mark.parametrize(
    "unsupported",
    [
        relation_mapping("IfcRelNests"),
        relation_mapping("IfcWall"),
        relation_mapping(match="subtypes"),
        mapping(
            {"kind": "object_property", "iri": EX + "hasPart", "object": "related", "cardinality": "many"},
            ifc_class="IfcRelAggregates",
            subject="entity",
        ),
        mapping(
            {"kind": "object_property", "iri": EX + "hasPart", "object": "relating", "cardinality": "many"},
            ifc_class="IfcRelAggregates",
            subject="relating",
        ),
    ],
)
def test_other_object_source_or_endpoint_roles_fail_even_without_records(unsupported):
    result = evaluate_ifc_mappings({"unsupported": unsupported}, [], "m")
    assert not result.assertions
    assert [item.code for item in result.diagnostics] == ["unsupported_relationship_mapping"]


def test_single_relationship_conflicts_across_explicit_relations_and_dedup_evidence():
    endpoints = [record("parent", "IfcBuildingStorey"), record("one", "IfcSpace"), record("two", "IfcSpace")]
    first = record("r1", "IfcRelAggregates", relating="parent", related=["one"])
    same = record("r2", "IfcRelAggregates", relating="parent", related=["one"])
    distinct = record("r3", "IfcRelAggregates", relating="parent", related=["two"])
    mappings = {"single": relation_mapping(cardinality="single"), "many": relation_mapping()}
    dedup = evaluate_ifc_mappings(mappings, endpoints + [first, same], "m")
    assert not dedup.diagnostics
    assert len(dedup.assertions) == 1
    assert {(item.mapping_id, item.source_id) for item in dedup.assertions[0].evidence} == {
        ("single", "r1"),
        ("single", "r2"),
        ("many", "r1"),
        ("many", "r2"),
    }
    conflict = evaluate_ifc_mappings(mappings, endpoints + [first, distinct], "m")
    assert not conflict.assertions
    assert [item.code for item in conflict.diagnostics] == ["cardinality_conflict"]


@pytest.mark.parametrize("model_id", ["", "   ", None, 1])
def test_missing_model_identity_fails_closed(model_id):
    result = evaluate_ifc_mappings({"class": mapping()}, [record()], model_id)
    assert not result.assertions
    assert [item.code for item in result.diagnostics] == ["invalid_model_id"]


def test_duplicate_record_ids_abort_entire_evaluation_deterministically():
    records = [record("duplicate"), record("duplicate", "IfcDoor"), record("otherwise_valid")]
    result = evaluate_ifc_mappings({"class": mapping()}, records, "m")
    assert not result.assertions
    assert [(item.code, item.source_id) for item in result.diagnostics] == [("duplicate_record_id", "duplicate")]
    assert result == evaluate_ifc_mappings({"class": mapping()}, list(reversed(records)), "m")
    assert not evaluate_ifc_mappings({"class": mapping()}, [record(), record()], "m").assertions


@pytest.mark.parametrize(
    "invalid_record",
    [
        {},
        {"id": "", "ifc_class": "IfcWall"},
        {"id": True, "ifc_class": "IfcWall"},
        {"id": "bad", "ifc_class": "IfcNotAnEntity"},
        {"id": "bad", "ifc_class": 1},
        record("bad", attributes=[]),
        record("bad", properties={"Pset_WallCommon": []}),
        record("bad", type_properties=None),
        record("bad", predefined_type=1),
    ],
)
def test_invalid_record_shape_diagnoses_instead_of_crashing(invalid_record):
    result = evaluate_ifc_mappings({"class": mapping()}, [record("valid"), invalid_record], "m")
    assert not result.assertions
    assert result.diagnostics
    assert {item.severity for item in result.diagnostics} == {"error"}


@pytest.mark.parametrize("subject", ["relating", "related"])
def test_class_and_literal_assertions_require_entity_subject(subject):
    for target in [{"kind": "class", "iri": EX + "Wall"}, constant_target(False)]:
        result = evaluate_ifc_mappings({"invalid": mapping(target, subject=subject)}, [record()], "m")
        assert not result.assertions
        assert [item.code for item in result.diagnostics] == ["unsupported_subject"]


def test_identity_components_are_unambiguous_and_not_double_decoded():
    result = evaluate_ifc_mappings({"class": mapping()}, [record("a/b"), record("a%2Fb")], "m:1")
    assert {item.subject for item in result.assertions} == {
        "urn:firebim:model:m%3A1:entity:a%2Fb",
        "urn:firebim:model:m%3A1:entity:a%252Fb",
    }
    same_local_id = evaluate_ifc_mappings({"class": mapping()}, [record("a/b")], "m:2")
    assert same_local_id.assertions[0].subject == "urn:firebim:model:m%3A2:entity:a%2Fb"


def union_mapping(*members, target=None, subject="entity", condition=None):
    definition = mapping(target, subject=subject, condition=condition).model_dump(mode="json")
    definition["source"] = {"schema": "IFC4", "any_of": list(members)}
    return ExecutableMapping.model_validate(definition)


def test_source_union_matches_exact_subtypes_and_per_member_predefined_types_once():
    selected = union_mapping(
        {"schema": "IFC4", "ifc_class": "IfcWall", "match": "subtypes", "predefined_type": "STANDARD"},
        {"schema": "IFC4", "ifc_class": "IfcWallStandardCase", "match": "exact", "predefined_type": "STANDARD"},
        {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact", "predefined_type": "DOOR"},
    )
    records = [
        record("wall", predefined_type="STANDARD"),
        record("subtype", "IfcWallStandardCase", predefined_type="STANDARD"),
        record("door", "IfcDoor", predefined_type="DOOR"),
        record("wrong-wall", predefined_type="USERDEFINED"),
        record("missing-wall"),
        record("wrong-door", "IfcDoor", predefined_type="USERDEFINED"),
        record("door-subtype", "IfcDoorStandardCase", predefined_type="DOOR"),
        record("other", "IfcSpace"),
    ]
    result = evaluate_ifc_mappings({"original-id": selected}, records, "m")
    assert not result.diagnostics
    assert [item.subject for item in result.assertions] == [
        "urn:firebim:model:m:entity:door",
        "urn:firebim:model:m:entity:subtype",
        "urn:firebim:model:m:entity:wall",
    ]
    assert all(len(item.evidence) == 1 and item.evidence[0].mapping_id == "original-id" for item in result.assertions)
    assert result == evaluate_ifc_mappings({"original-id": selected}, list(reversed(records)), "m")


def test_source_union_value_reads_conditions_and_diagnostics_keep_original_mapping_id():
    selected = union_mapping(
        {"schema": "IFC4", "ifc_class": "IfcWall", "match": "subtypes"},
        {"schema": "IFC4", "ifc_class": "IfcWallStandardCase", "match": "exact"},
        {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact"},
        target=read_target(selector(required=True)),
        condition={"kind": "present", "selector": selector("Name", "string", kind="attribute")},
    )
    result = evaluate_ifc_mappings(
        {"original-value-id": selected},
        [
            record(
                "wall",
                "IfcWallStandardCase",
                attributes={"Name": "Wall"},
                properties={"Pset_WallCommon": {"IsExternal": False}},
            ),
            record(
                "door", "IfcDoor", attributes={"Name": "Door"}, properties={"Pset_WallCommon": {"IsExternal": True}}
            ),
            record("invalid", attributes={"Name": "Invalid"}, properties={"Pset_WallCommon": {"IsExternal": "true"}}),
            record("missing", attributes={"Name": "Missing"}),
        ],
        "m",
    )
    assert [(item.subject, item.object) for item in result.assertions] == [
        ("urn:firebim:model:m:entity:door", True),
        ("urn:firebim:model:m:entity:wall", False),
    ]
    assert all(len(item.evidence) == 2 for item in result.assertions)
    assert {evidence.mapping_id for item in result.assertions for evidence in item.evidence} == {"original-value-id"}
    assert {(item.code, item.source_id, item.mapping_id) for item in result.diagnostics} == {
        ("invalid_source_value", "invalid", "original-value-id"),
        ("missing_required", "missing", "original-value-id"),
    }


@pytest.mark.parametrize("with_records", [False, True])
def test_object_source_union_fails_closed_without_semantic_validation(with_records):
    selected = union_mapping(
        {"schema": "IFC4", "ifc_class": "IfcRelAggregates", "match": "exact"},
        {"schema": "IFC4", "ifc_class": "IfcRelContainedInSpatialStructure", "match": "exact"},
        target={"kind": "object_property", "iri": EX + "hasPart", "object": "related", "cardinality": "many"},
        subject="relating",
    )
    records = (
        [
            record("parent", "IfcBuildingStorey"),
            record("child", "IfcSpace"),
            record("relation", "IfcRelAggregates", relating="parent", related=["child"]),
        ]
        if with_records
        else []
    )
    result = evaluate_ifc_mappings({"original-relation-id": selected}, records, "m")
    assert not result.assertions
    assert [(item.code, item.mapping_id) for item in result.diagnostics] == [
        ("unsupported_relationship_mapping", "original-relation-id"),
    ]


def measurement_selector(*, kind="property", unit="ifc_area", required=False):
    return {
        "kind": kind,
        "pset": "CPset_FireBIM" if kind == "property" else "Qto_SpatialZoneOtherBaseQuantities",
        "name": "CompartmentArea" if kind == "property" else "NetFloorArea",
        "datatype": XSD + "double",
        "unit": unit,
        "required": required,
    }


def measurement_mapping(*, fallback=False, required=False, cardinality="single"):
    target = read_target(measurement_selector(required=required), datatype="double", cardinality=cardinality)
    if fallback:
        target["value"]["fallback"] = [measurement_selector(kind="quantity")]
    return mapping(target, ifc_class="IfcSpatialZone")


def unit(symbol="m2", dimension="area", scale=1):
    return {"symbol": symbol, "dimension": dimension, "scale_to_si": scale}


def area_record(value=None, *, quantity=None, **fields):
    return record(
        ifc_class="IfcSpatialZone",
        properties={"CPset_FireBIM": {"CompartmentArea": value}},
        quantities={"Qto_SpatialZoneOtherBaseQuantities": {"NetFloorArea": quantity}},
        **fields,
    )


@pytest.mark.parametrize(
    ("model_units", "value", "symbol", "scale", "origin"),
    [
        (None, 2, "m2", 1, "si_default"),
        ({"area": unit("cm2", scale=0.0001)}, 200, "cm2", 0.0001, "model"),
        ({"length": unit("cm", "length", 0.01)}, 200, "cm2", 0.0001, "model_length_squared"),
        ({"area": unit("cm2", scale=0.0001)}, {"value": 2, "unit": unit()}, "m2", 1, "value"),
    ],
)
def test_measurement_units_preserve_numeric_source_value(model_units, value, symbol, scale, origin):
    result = evaluate_ifc_mappings({"area": measurement_mapping()}, [area_record(value)], "m", model_units=model_units)
    assert not result.diagnostics
    (assertion,) = result.assertions
    assert assertion.object == (value["value"] if isinstance(value, dict) else value)
    assert (assertion.unit.symbol, assertion.unit.dimension, assertion.unit.scale_to_si) == (symbol, "area", scale)
    assert assertion.evidence[0].unit_origin == origin


@pytest.mark.parametrize(
    "value",
    [
        True,
        "2",
        float("inf"),
        {"value": 2},
        {"value": 2, "unit": None},
        {"value": True, "unit": unit()},
        {"value": "2", "unit": unit()},
        {"value": 2, "unit": unit("m", "length")},
        {"value": 2, "unit": unit(scale=0)},
        {"value": 2, "unit": unit(scale=-1)},
        {"value": 2, "unit": unit(scale=float("inf"))},
        {"value": 2, "unit": unit(scale=True)},
        {"value": 2, "unit": unit(scale="1")},
        {"value": 2, "unit": unit(" ")},
        {"value": 2, "unit": {**unit(), "extra": 1}},
        {"value": 2, "unit": unit(), "extra": 1},
    ],
)
def test_invalid_measurement_never_falls_back_to_quantity_or_assumed_units(value):
    result = evaluate_ifc_mappings({"area": measurement_mapping(fallback=True)}, [area_record(value, quantity=3)], "m")
    assert not result.assertions
    assert any(item.code == "invalid_source_value" for item in result.diagnostics)


@pytest.mark.parametrize(
    "model_units",
    [
        [],
        {"volume": unit()},
        {"area": None},
        {"length": unit()},
        {"area": unit(scale=False)},
        {"length": unit("m", "length", 1e300)},
    ],
)
def test_invalid_model_units_fail_closed(model_units):
    result = evaluate_ifc_mappings({"area": measurement_mapping()}, [area_record(2)], "m", model_units=model_units)
    assert not result.assertions
    assert any(item.code == "invalid_model_units" for item in result.diagnostics)


def test_quantity_fallback_missing_primary_type_merge_and_evidence():
    selected = measurement_mapping(fallback=True, required=True)
    result = evaluate_ifc_mappings(
        {"area": selected},
        [
            area_record(
                quantity=None,
                type_quantities={"Qto_SpatialZoneOtherBaseQuantities": {"NetFloorArea": 9}},
            )
        ],
        "m",
    )
    assert not result.assertions  # Explicit occurrence null masks type quantity.
    assert [item.code for item in result.diagnostics] == ["missing_required"]
    inherited = record(
        ifc_class="IfcSpatialZone",
        quantities={"Qto_SpatialZoneOtherBaseQuantities": {"Other": 1}},
        type_quantities={"Qto_SpatialZoneOtherBaseQuantities": {"NetFloorArea": 9}},
    )
    result = evaluate_ifc_mappings({"area": selected}, [inherited], "m")
    assert not result.diagnostics
    (assertion,) = result.assertions
    assert assertion.object == 9
    assert (assertion.evidence[0].kind, assertion.evidence[0].origin) == ("quantity", "type")


@pytest.mark.parametrize(
    ("quantity", "warning"),
    [
        ({"value": 10000, "unit": unit("cm2", scale=0.0001)}, False),
        (2, True),
    ],
)
def test_measurement_fallback_compares_every_source_physically(quantity, warning):
    result = evaluate_ifc_mappings(
        {"area": measurement_mapping(fallback=True)},
        [area_record(1, quantity=quantity)],
        "m",
    )
    (assertion,) = result.assertions
    assert assertion.object == 1 and assertion.unit.symbol == "m2"
    assert {item.kind for item in assertion.evidence} == {"property", "quantity"}
    assert [(item.code, item.severity) for item in result.diagnostics] == (
        [("mapping_source_disagreement", "warning")] if warning else []
    )


@pytest.mark.parametrize("quantity", ["bad", [1, 2], [1, {"value": 20000, "unit": unit("cm2", scale=0.0001)}]])
def test_invalid_or_conflicting_fallback_is_checked_even_when_primary_present(quantity):
    result = evaluate_ifc_mappings(
        {"area": measurement_mapping(fallback=True)},
        [area_record(1, quantity=quantity)],
        "m",
    )
    assert not result.assertions
    assert any(item.severity == "error" for item in result.diagnostics)


def test_physical_assertion_deduplication_and_conflicts_across_mappings():
    primary = measurement_mapping()
    other = mapping(read_target(measurement_selector(kind="quantity"), datatype="double"), ifc_class="IfcSpatialZone")
    for quantity, conflict in [({"value": 10000, "unit": unit("cm2", scale=0.0001)}, False), (2, True)]:
        result = evaluate_ifc_mappings(
            {"property": primary, "quantity": other},
            [area_record(1, quantity=quantity)],
            "m",
        )
        if conflict:
            assert not result.assertions
            assert [item.code for item in result.diagnostics] == ["cardinality_conflict"]
        else:
            (assertion,) = result.assertions
            assert assertion.object == 1 and assertion.unit.symbol == "m2"
            assert {item.mapping_id for item in assertion.evidence} == {"property", "quantity"}


def grouped_mapping(*, iri=EX + "Compartment", condition=None):
    return mapping(
        {"kind": "class", "iri": iri, "grouping": selector("CompartmentName", "string", pset="CPset_FireBIM")},
        ifc_class="IfcSpace",
        condition=condition,
    )


def test_grouped_class_has_model_class_and_exact_name_identity_and_memberships_not_space_types():
    records = [
        record(name, "IfcSpace", properties={"CPset_FireBIM": {"CompartmentName": value}})
        for name, value in [
            ("a", "North"),
            ("b", "North"),
            ("c", " North"),
            ("d", "north"),
            ("blank", "  \t"),
            ("missing", None),
        ]
    ]
    selected = grouped_mapping()
    result = evaluate_ifc_mappings({"group": selected}, records, "m")
    assert not result.diagnostics
    assert len(result.assertions) == 3 and len(result.memberships) == 4
    assert all(":group:" in item.subject and ":entity:" not in item.subject for item in result.assertions)
    assert all(item.predicate.endswith("#type") and item.object == EX + "Compartment" for item in result.assertions)
    membership = {item.member: item for item in result.memberships}
    assert membership["urn:firebim:model:m:entity:a"].group == membership["urn:firebim:model:m:entity:b"].group
    assert len({item.group for item in result.memberships}) == 3
    assert all(item.evidence[0].name == "CompartmentName" for item in result.memberships)
    assert result == evaluate_ifc_mappings({"group": selected}, list(reversed(records)), "m")
    other = evaluate_ifc_mappings({"group": selected}, records, "other")
    assert not {item.subject for item in result.assertions} & {item.subject for item in other.assertions}
    different_class = evaluate_ifc_mappings({"group": grouped_mapping(iri=EX + "Other")}, records, "m")
    assert not {item.subject for item in result.assertions} & {item.subject for item in different_class.assertions}


def test_grouping_checks_conditions_and_invalid_multivalues_without_first_wins():
    selected = grouped_mapping(condition={"kind": "equals", "selector": selector(), "values": [True]})
    records = [
        record(
            "valid",
            "IfcSpace",
            properties={"CPset_FireBIM": {"CompartmentName": "A"}, "Pset_WallCommon": {"IsExternal": False}},
        )
    ]
    result = evaluate_ifc_mappings({"group": selected}, records, "m")
    assert not result.assertions and not result.memberships
    for value in [True, ["A", "B"]]:
        result = evaluate_ifc_mappings(
            {"group": grouped_mapping()},
            [record(ifc_class="IfcSpace", properties={"CPset_FireBIM": {"CompartmentName": value}})],
            "m",
        )
        assert not result.assertions and not result.memberships
        assert any(item.severity == "error" for item in result.diagnostics)


@pytest.mark.parametrize(
    ("model_units", "value", "symbol", "scale"),
    [
        (None, 2, "m", 1),
        ({"length": unit("mm", "length", 0.001)}, 200, "mm", 0.001),
        ({"length": unit("mm", "length", 0.001)}, {"value": 2, "unit": unit("cm", "length", 0.01)}, "cm", 0.01),
    ],
)
def test_length_measurements_use_value_model_or_si_units(model_units, value, symbol, scale):
    selected = mapping(
        read_target(measurement_selector(unit="ifc_length"), datatype="double"),
        ifc_class="IfcSpatialZone",
    )
    result = evaluate_ifc_mappings({"length": selected}, [area_record(value)], "m", model_units=model_units)
    assert not result.diagnostics
    (assertion,) = result.assertions
    assert (assertion.unit.symbol, assertion.unit.dimension, assertion.unit.scale_to_si) == (symbol, "length", scale)


def test_model_area_takes_precedence_over_length_and_measurement_lists_merge_physically():
    result = evaluate_ifc_mappings(
        {"area": measurement_mapping(cardinality="many")},
        [
            area_record(
                [
                    1,
                    {"value": 10000, "unit": unit("cm2", scale=0.0001)},
                ]
            )
        ],
        "m",
        model_units={"length": unit("cm", "length", 0.01), "area": unit()},
    )
    assert not result.diagnostics
    (assertion,) = result.assertions
    assert assertion.object == 1 and assertion.unit.symbol == "m2"
    assert {item.unit_origin for item in assertion.evidence} == {"model", "value"}


def test_quantity_maps_never_read_properties_with_same_names():
    selected = mapping(
        read_target(measurement_selector(kind="quantity"), datatype="double"),
        ifc_class="IfcSpatialZone",
    )
    records = [
        record(
            ifc_class="IfcSpatialZone",
            properties={"Qto_SpatialZoneOtherBaseQuantities": {"NetFloorArea": 99}},
            type_quantities={"Qto_SpatialZoneOtherBaseQuantities": {"NetFloorArea": 2}},
        )
    ]
    result = evaluate_ifc_mappings({"quantity": selected}, records, "m")
    assert not result.diagnostics
    (assertion,) = result.assertions
    assert assertion.object == 2 and assertion.evidence[0].origin == "type"


def test_four_fallback_sources_use_first_present_and_validate_the_last():
    definition = measurement_mapping(fallback=True).model_dump(mode="json")
    selected = definition["target"]["value"]["fallback"][0]
    definition["target"]["value"]["fallback"] = [{**selected, "name": f"Area{index}"} for index in range(4)]
    compiled = ExecutableMapping.model_validate(definition)
    values = {"Area1": 2, "Area2": 2, "Area3": 3}
    records = [record(ifc_class="IfcSpatialZone", quantities={selected["pset"]: values})]
    result = evaluate_ifc_mappings({"area": compiled}, records, "m")
    (assertion,) = result.assertions
    assert assertion.object == 2 and {item.name for item in assertion.evidence} == {"Area1", "Area2", "Area3"}
    assert [(item.code, item.severity) for item in result.diagnostics] == [("mapping_source_disagreement", "warning")]
    values["Area3"] = "invalid"
    invalid = evaluate_ifc_mappings({"area": compiled}, records, "m")
    assert not invalid.assertions
    assert [item.code for item in invalid.diagnostics] == ["invalid_source_value"]


def test_group_memberships_merge_mapping_provenance_and_preserve_type_source():
    records = [record("space", "IfcSpace", type_properties={"CPset_FireBIM": {"CompartmentName": ["A", "A"]}})]
    result = evaluate_ifc_mappings({"a": grouped_mapping(), "b": grouped_mapping()}, records, "m")
    assert not result.diagnostics
    (assertion,) = result.assertions
    (membership,) = result.memberships
    assert membership.group == assertion.subject and membership.member == "urn:firebim:model:m:entity:space"
    assert {item.mapping_id for item in membership.evidence} == {"a", "b"}
    assert {item.origin for item in membership.evidence} == {"type"}
