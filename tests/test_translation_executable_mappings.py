import copy
import hashlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.lexicon.translation_validation import validate_translation_draft
from app.schemas.ifc_mapping import ExecutableMapping
from app.schemas.translation import TranslationContract, TranslationStoredProfile

XSD = "http://www.w3.org/2001/XMLSchema#"
FBO = "https://example.org/fbo#"


def measurement_definition():
    return value_definition(
        "integer",
        {
            "kind": "read",
            "selector": {
                "kind": "property",
                "pset": "CPset_FireBIM",
                "name": "CompartmentArea",
                "datatype": XSD + "integer",
                "unit": "ifc_area",
            },
            "fallback": [
                {
                    "kind": "quantity",
                    "pset": "Qto_SpatialZoneOtherBaseQuantities",
                    "name": "NetFloorArea",
                    "datatype": XSD + "integer",
                    "unit": "ifc_area",
                }
            ],
        },
        iri=FBO + "Number",
    )


def test_measurement_and_grouping_shared_language_validate_without_relaxing_target_kind():
    area = measurement_definition()
    assert validate_translation_draft(profile(area), Releases()).authoring_valid
    grouped = definition(
        target={
            "kind": "class",
            "iri": FBO + "Wall",
            "grouping": {
                "kind": "property",
                "pset": "CPset_FireBIM",
                "name": "CompartmentName",
                "datatype": XSD + "string",
            },
        }
    )
    assert validate_translation_draft(profile(grouped), Releases()).authoring_valid
    grouped["target"]["iri"] = FBO + "Number"
    assert any(
        d.code == "mapping_target_kind_mismatch"
        for d in validate_translation_draft(profile(grouped), Releases()).diagnostics
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"pset": None},
        {"unit": "unitless"},
        {"unit": None},
        {"datatype": XSD + "string"},
    ],
)
def test_quantity_requires_exact_context_numeric_datatype_and_measurement_unit(patch):
    area = measurement_definition()
    area["target"]["value"]["fallback"][0].update(patch)
    assert not validate_translation_draft(profile(area), Releases()).authoring_valid


@pytest.mark.parametrize("patch", [{"unit": "ifc_length"}, {"datatype": XSD + "double"}])
def test_fallback_selectors_require_same_datatype_and_unit(patch):
    area = measurement_definition()
    area["target"]["value"]["fallback"][0].update(patch)
    assert not validate_translation_draft(profile(area), Releases()).authoring_valid


def test_fallback_bounded_and_measurement_only_and_unit_target_fail_closed():
    area = measurement_definition()
    area["target"]["value"]["fallback"] *= 5
    assert not validate_translation_draft(profile(area), Releases()).authoring_valid
    area = measurement_definition()
    area["target"]["value"]["selector"]["unit"] = "unitless"
    area["target"]["value"]["fallback"][0].update(kind="property", unit="unitless")
    assert not validate_translation_draft(profile(area), Releases()).authoring_valid
    area = measurement_definition()
    assert not validate_translation_draft(
        profile(area), Releases(TTL + '\nf:Number <http://schema.org/unitCode> "m2" .')
    ).authoring_valid
    area["target"]["datatype"] = XSD + "string"
    area["target"]["iri"] = FBO + "Name"
    area["target"]["value"]["transform"] = "lookup"
    area["target"]["value"]["lookup"] = [{"input": 1, "output": "one"}]
    assert not validate_translation_draft(profile(area), Releases()).authoring_valid


@pytest.mark.parametrize(
    "patch",
    [
        {"kind": "attribute", "pset": None, "name": "Name"},
        {"kind": "quantity"},
        {"datatype": XSD + "integer", "unit": "unitless"},
    ],
)
def test_grouping_only_accepts_string_property_selectors(patch):
    selected = {"kind": "property", "pset": "CPset_FireBIM", "name": "CompartmentName", "datatype": XSD + "string"}
    selected.update(patch)
    grouped = definition(target={"kind": "class", "iri": FBO + "Wall", "grouping": selected})
    assert not validate_translation_draft(profile(grouped), Releases()).authoring_valid


def test_redirected_class_requires_actual_declaration_and_unit_annotation_is_not_execution_metadata():
    iri = "https://firebim.org/ontologies/fbo/dk#Compartment"
    grouped = definition(
        target={
            "kind": "class",
            "iri": iri,
            "grouping": {
                "kind": "property",
                "pset": "CPset_FireBIM",
                "name": "CompartmentName",
                "datatype": XSD + "string",
            },
        }
    )
    assert validate_translation_draft(profile(grouped), Releases(TTL + f"\n<{iri}> a owl:Class .")).authoring_valid
    assert not validate_translation_draft(profile(grouped), Releases()).authoring_valid
    assert not validate_translation_draft(
        profile(grouped), Releases(TTL + f"\n<{iri}> a owl:DatatypeProperty ; rdfs:range xsd:string .")
    ).authoring_valid
    area = measurement_definition()
    assert validate_translation_draft(
        profile(area),
        Releases(TTL + '\nf:Number <https://firebim.org/ontologies/fbo#applicableUnit> "model area unit" .'),
    ).authoring_valid


TTL = """
@prefix f: <https://example.org/fbo#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
f: a owl:Ontology ; owl:versionIRI <https://example.org/fbo/2026.09.04#> .
f:Wall a owl:Class .
f:External a owl:DatatypeProperty ; rdfs:range xsd:boolean .
f:Name a owl:DatatypeProperty ; rdfs:range xsd:string .
f:Number a owl:DatatypeProperty ; rdfs:range xsd:integer .
f:Contains a owl:ObjectProperty .
"""


class Releases:
    def __init__(self, ttl=TTL):
        self.ttl = ttl

    def fetch_version(self, family, country, version):
        assert (family, country, version) == ("fbo", "core", "2026.09.04")
        return SimpleNamespace(
            ttl=self.ttl,
            version=SimpleNamespace(
                version=version,
                ontology_uri=FBO,
                version_iri="https://example.org/fbo/2026.09.04#",
                sha256=hashlib.sha256(self.ttl.encode()).hexdigest(),
            ),
        )


def definition(**changes):
    return {
        "language": "ifc4-mapping/1",
        "source": {"schema": "IFC4", "ifc_class": "IfcWall", "match": "subtypes"},
        "subject": "entity",
        "target": {"kind": "class", "iri": FBO + "Wall"},
        "policies": {
            "identity": "model_scoped",
            "property_precedence": "occurrence_over_type",
            "missing": "skip",
            "invalid": "error",
            "conflicts": "error",
        },
        **changes,
    }


def profile(*definitions):
    return TranslationStoredProfile(
        id="ifct_test",
        name="IFC4 pilot",
        created_at="now",
        updated_at="now",
        contract=TranslationContract(
            destination_modules=[{"family": "fbo", "country": "core"}],
            mappings=[
                {"id": f"mapping-{index}", "enabled": True, "review_state": "accepted", "definition": item}
                for index, item in enumerate(definitions)
            ],
        ),
    )


def test_valid_ifc4_class_mapping_passes_authoring_without_claiming_upload_readiness():
    draft = profile(definition())
    before = draft.model_dump(mode="json")
    report = validate_translation_draft(draft, Releases())
    assert report.authoring_valid is True
    assert [step.status for step in report.steps] == ["passed", "passed", "passed"]
    assert report.runtime_available is False and report.publish_ready is True
    assert not any(d.severity == "error" for d in report.diagnostics)
    assert draft.model_dump(mode="json") == before


def value_definition(datatype="boolean", value=None, **changes):
    return definition(
        target={
            "kind": "datatype_property",
            "iri": FBO + "External",
            "datatype": XSD + datatype,
            "value": {"kind": "constant", "value": True} if value is None else value,
            "cardinality": "single",
            **changes,
        }
    )


def test_boolean_property_checks_kind_exact_iri_range_and_literal_type():
    valid = value_definition()
    assert validate_translation_draft(profile(valid), Releases()).authoring_valid
    for field, value, code in [
        ("value", {"kind": "constant", "value": "True"}, "mapping_literal_invalid"),
        ("value", {"kind": "constant", "value": 1}, "mapping_literal_invalid"),
        ("datatype", XSD + "string", "mapping_target_datatype_mismatch"),
        ("iri", "https://other.example/fbo#External", "mapping_target_not_declared"),
    ]:
        invalid = copy.deepcopy(valid)
        invalid["target"][field] = value
        report = validate_translation_draft(profile(invalid), Releases())
        assert not report.authoring_valid
        assert any(d.code == code and d.mapping_id == "mapping-0" for d in report.diagnostics)
    wrong_kind = definition(target={"kind": "class", "iri": FBO + "External"})
    assert any(
        d.code == "mapping_target_kind_mismatch"
        for d in validate_translation_draft(profile(wrong_kind), Releases()).diagnostics
    )


@pytest.mark.parametrize(
    "source",
    [
        {"schema": "IFC4X3", "ifc_class": "IfcWall", "match": "exact"},
        {"schema": "IFC4", "ifc_class": "IfcNotAnEntity", "match": "exact"},
        {"schema": "IFC4", "ifc_class": "IfcWall", "match": "first"},
        {"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact", "predefined_type": "MADEUP"},
    ],
)
def test_source_schema_class_matching_and_predefined_enum_are_checked(source):
    report = validate_translation_draft(profile(definition(source=source)), Releases())
    assert not report.authoring_valid
    assert any(
        d.severity == "error" and d.field_path.startswith("contract.mappings[0].definition.source")
        for d in report.diagnostics
    )


def test_condition_total_size_is_bounded_not_only_depth():
    leaf = {"kind": "present", "selector": {"kind": "attribute", "name": "Name", "datatype": XSD + "string"}}
    condition = {
        "kind": "all_of",
        "conditions": [
            {"kind": "any_of", "conditions": [leaf] * 20},
            {"kind": "any_of", "conditions": [leaf] * 20},
        ],
    }
    report = validate_translation_draft(profile(definition(condition=condition)), Releases())
    assert not report.authoring_valid
    assert any(d.code == "mapping_condition_too_large" for d in report.diagnostics)


def test_exact_property_and_attribute_selectors_validate_with_typed_conditions():
    selector = {"kind": "property", "pset": "Pset_WallCommon", "name": "IsExternal", "datatype": XSD + "boolean"}
    valid = value_definition(value={"kind": "read", "selector": selector})
    valid["condition"] = {
        "kind": "all_of",
        "conditions": [
            {"kind": "equals", "selector": selector, "values": [True]},
            {"kind": "present", "selector": {"kind": "attribute", "name": "Name", "datatype": XSD + "string"}},
        ],
    }
    assert validate_translation_draft(profile(valid), Releases()).authoring_valid
    for patch in [
        {"pset": "Pset_*Common"},
        {"pset": None},
        {"name": "A/B"},
        {"kind": "attribute", "pset": None, "name": "FakeAttribute"},
        {"kind": "attribute", "pset": None, "name": "Name"},
        {"name": "FireRating"},
    ]:
        invalid = copy.deepcopy(valid)
        invalid["target"]["value"]["selector"].update(patch)
        assert not validate_translation_draft(profile(invalid), Releases()).authoring_valid
    invalid = copy.deepcopy(valid)
    invalid["condition"]["conditions"][0]["values"] = [1]
    assert any(
        d.code == "mapping_condition_value_invalid"
        for d in validate_translation_draft(profile(invalid), Releases()).diagnostics
    )


def test_unsupported_languages_scripts_fields_conditions_and_policies_fail_closed():
    leaf = {"kind": "present", "selector": {"kind": "attribute", "name": "Name", "datatype": XSD + "string"}}
    deep = leaf
    for _ in range(4):
        deep = {"kind": "all_of", "conditions": [deep]}
    for invalid in [
        definition(language="python"),
        definition(script="return True"),
        definition(condition=deep),
        definition(condition={"kind": "sparql", "query": "SELECT * WHERE {?s ?p ?o}"}),
        definition(policies={"conflicts": "first_value_wins"}),
        definition(source={"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact", "path": "*"}),
    ]:
        report = validate_translation_draft(profile(invalid), Releases())
        assert not report.authoring_valid
        assert any(d.severity == "error" for d in report.diagnostics)


def test_numeric_casts_are_checked_and_never_parse_rating_strings():
    read = {
        "kind": "read",
        "selector": {
            "kind": "property",
            "pset": "Custom",
            "name": "Count",
            "datatype": XSD + "double",
            "unit": "unitless",
        },
        "transform": "numeric_cast",
    }
    valid = value_definition("integer", read, iri=FBO + "Number")
    assert validate_translation_draft(profile(valid), Releases()).authoring_valid
    for patch, code in [
        ({"unit": None}, "mapping_units_required"),
        ({"datatype": XSD + "string", "unit": None}, "mapping_numeric_cast_invalid"),
        ({"name": "FireRating", "datatype": XSD + "string", "unit": None}, "mapping_interpretation_required"),
    ]:
        invalid = copy.deepcopy(valid)
        invalid["target"]["value"]["selector"].update(patch)
        assert any(d.code == code for d in validate_translation_draft(profile(invalid), Releases()).diagnostics)
    for literal in ["REI60", True, "60"]:
        invalid = value_definition("integer", {"kind": "constant", "value": literal}, iri=FBO + "Number")
        assert any(
            d.code == "mapping_literal_invalid"
            for d in validate_translation_draft(profile(invalid), Releases()).diagnostics
        )


def test_unresolved_fire_resistance_target_cannot_be_enabled_even_as_a_manual_constant():
    iri = "https://firebim.org/ontologies/fbo#FireResistance"
    ttl = TTL + f"\n<{iri}> a owl:DatatypeProperty ; rdfs:range xsd:float ."
    invalid = value_definition("float", {"kind": "constant", "value": 60}, iri=iri)
    report = validate_translation_draft(profile(invalid), Releases(ttl))
    assert not report.authoring_valid
    assert any(d.code == "mapping_interpretation_required" for d in report.diagnostics)


def test_lookup_checks_typed_unique_inputs_and_typed_outputs():
    read = {
        "kind": "read",
        "selector": {"kind": "property", "pset": "Custom", "name": "Flag", "datatype": XSD + "string"},
        "transform": "lookup",
        "lookup": [{"input": "yes", "output": True}, {"input": "no", "output": False}],
    }
    assert validate_translation_draft(profile(value_definition(value=read)), Releases()).authoring_valid
    for table in [
        [],
        [{"input": "yes", "output": "true"}],
        [{"input": "yes", "output": True}, {"input": "yes", "output": False}],
        [{"input": 1, "output": True}],
    ]:
        invalid = {**read, "lookup": table}
        assert not validate_translation_draft(profile(value_definition(value=invalid)), Releases()).authoring_valid


@pytest.mark.parametrize(
    "ttl",
    [
        TTL + "\nf:External rdfs:range xsd:string .",
        TTL + "\nf:External a owl:Class .",
        TTL.replace("; rdfs:range xsd:boolean", ""),
        TTL + '\nf:External <http://schema.org/unitCode> "m" .',
    ],
)
def test_ambiguous_kinds_ranges_missing_ranges_and_units_cannot_pass(ttl):
    assert not validate_translation_draft(profile(value_definition()), Releases(ttl)).authoring_valid


def test_single_value_conflicts_are_errors_and_identical_duplicates_are_allowed():
    first, second = value_definition(), value_definition(value={"kind": "constant", "value": False})
    report = validate_translation_draft(profile(first, second), Releases())
    assert any(d.code == "mapping_output_conflict" for d in report.diagnostics)
    assert not report.authoring_valid
    assert validate_translation_draft(profile(first, first), Releases()).authoring_valid
    second["source"] = {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact"}
    assert validate_translation_draft(profile(first, second), Releases()).authoring_valid
    many = value_definition(cardinality="many")
    report = validate_translation_draft(profile(many), Releases(TTL + "\nf:External a owl:FunctionalProperty ."))
    assert any(d.code == "mapping_cardinality_invalid" for d in report.diagnostics)


@pytest.mark.parametrize("relationship", ["IfcRelAggregates", "IfcRelContainedInSpatialStructure"])
def test_relationships_require_exact_one_hop_and_explicit_opposite_roles(relationship):
    valid = definition(
        source={"schema": "IFC4", "ifc_class": relationship, "match": "exact"},
        subject="relating",
        target={"kind": "object_property", "iri": FBO + "Contains", "object": "related", "cardinality": "many"},
    )
    assert validate_translation_draft(profile(valid), Releases()).authoring_valid
    for invalid in [
        {**valid, "subject": "entity"},
        {**valid, "subject": "related"},
        {**valid, "source": {**valid["source"], "match": "subtypes"}},
        {**valid, "source": {**valid["source"], "ifc_class": "IfcRelSpaceBoundary"}},
        {**valid, "source": {**valid["source"], "ifc_class": "IfcWall"}},
    ]:
        assert not validate_translation_draft(profile(invalid), Releases()).authoring_valid


def test_invalid_destination_or_outage_prevents_false_mapping_pass():
    draft = profile(definition())
    draft.contract.destination_modules[0].sha256 = "0" * 64
    report = validate_translation_draft(draft, Releases())
    assert not report.authoring_valid
    assert report.steps[1].status == "unavailable"
    assert any(d.code == "mapping_validation_unavailable" for d in report.diagnostics)


def union_source(*members):
    return {"schema": "IFC4", "any_of": list(members)}


def test_source_union_accepts_full_selectors_with_predefined_types_and_preserves_shape():
    from app.schemas.ifc_mapping import EntitySelector, EntityUnionSelector, source_selectors

    members = [
        {"schema": "IFC4", "ifc_class": "IfcWall", "match": "subtypes", "predefined_type": "STANDARD"},
        {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact", "predefined_type": "DOOR"},
    ]
    source = union_source(*members)
    executable = ExecutableMapping.model_validate(definition(source=source))
    assert isinstance(executable.source, EntityUnionSelector)
    assert list(source_selectors(executable.source)) == executable.source.any_of
    assert all(isinstance(member, EntitySelector) for member in source_selectors(executable.source))
    assert executable.model_dump(mode="json", exclude_none=True)["source"] == source
    assert validate_translation_draft(profile(definition(source=source)), Releases()).authoring_valid
    single = ExecutableMapping.model_validate(definition())
    assert isinstance(single.source, EntitySelector)
    assert list(source_selectors(single.source)) == [single.source]
    bounded = definition(source=union_source(*([members[0]] * 32)))
    assert validate_translation_draft(profile(bounded), Releases()).authoring_valid


@pytest.mark.parametrize(
    "source",
    [
        {"schema": "IFC4", "any_of": []},
        union_source({"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"}),
        union_source(*([{"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"}] * 33)),
        union_source(
            {"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"},
            union_source(*([{"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact"}] * 2)),
        ),
        {
            "schema": "IFC4",
            "ifc_class": "IfcWall",
            "match": "exact",
            "any_of": [
                {"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"},
                {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact"},
            ],
        },
        {"any_of": [{"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"}] * 2},
        {"schema": "IFC4X3", "any_of": [{"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"}] * 2},
        {
            "schema": "IFC4",
            "unexpected": True,
            "any_of": [{"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"}] * 2,
        },
    ],
)
def test_source_union_rejects_unbounded_nested_mixed_and_unknown_shapes(source):
    with pytest.raises(ValidationError):
        ExecutableMapping.model_validate(definition(source=source))
    report = validate_translation_draft(profile(definition(source=source)), Releases())
    assert not report.authoring_valid
    assert any(d.code == "mapping_definition_invalid" for d in report.diagnostics)


@pytest.mark.parametrize(
    "member",
    [
        {"ifc_class": "IfcDoor", "match": "exact"},
        {"schema": "IFC4X3", "ifc_class": "IfcDoor", "match": "exact"},
        {"schema": "IFC4", "match": "exact"},
        {"schema": "IFC4", "ifc_class": "IfcDoor"},
        {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "EXACT"},
        {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact", "path": "*"},
        {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact", "any_of": []},
    ],
)
def test_source_union_rejects_incomplete_or_invalid_nonfirst_member(member):
    source = union_source({"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"}, member)
    with pytest.raises(ValidationError):
        ExecutableMapping.model_validate(definition(source=source))


@pytest.mark.parametrize(
    ("member", "code", "field"),
    [
        ({"schema": "IFC4", "ifc_class": "IfcFake", "match": "exact"}, "mapping_source_class_unsupported", "ifc_class"),
        (
            {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact", "predefined_type": "STANDARD"},
            "mapping_predefined_type_invalid",
            "predefined_type",
        ),
    ],
)
def test_source_union_validates_nonfirst_class_and_enum_with_indexed_path(member, code, field):
    source = union_source({"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"}, member)
    report = validate_translation_draft(profile(definition(source=source)), Releases())
    assert not report.authoring_valid
    assert any(
        d.code == code and d.field_path == f"contract.mappings[0].definition.source.any_of[1].{field}"
        for d in report.diagnostics
    )


@pytest.mark.parametrize("location", ["read", "condition"])
def test_source_union_attribute_checks_cover_every_member(location):
    selected = {"kind": "attribute", "name": "Name", "datatype": XSD + "string"}
    valid = (
        value_definition("string", {"kind": "read", "selector": selected}, iri=FBO + "Name")
        if location == "read"
        else definition(
            condition={"kind": "any_of", "conditions": [{"kind": "present", "selector": selected}]},
        )
    )
    valid["source"] = union_source(
        {"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"},
        {"schema": "IFC4", "ifc_class": "IfcProject", "match": "exact"},
    )
    assert validate_translation_draft(profile(valid), Releases()).authoring_valid
    invalid = copy.deepcopy(valid)
    field = "target.value.selector" if location == "read" else "condition.conditions[0].selector"
    selected = (
        invalid["target"]["value"]["selector"]
        if location == "read"
        else invalid["condition"]["conditions"][0]["selector"]
    )
    selected["name"] = "Tag"
    report = validate_translation_draft(profile(invalid), Releases())
    assert not report.authoring_valid
    assert any(
        d.code == "mapping_attribute_unsupported"
        and d.field_path.endswith(field + ".name")
        and "source.any_of[1]" in d.message
        for d in report.diagnostics
    )


@pytest.mark.parametrize(
    ("union_match", "other_class", "other_match", "other_predefined", "conflict"),
    [
        ("subtypes", "IfcWallStandardCase", "exact", "STANDARD", True),
        ("exact", "IfcWallStandardCase", "exact", "STANDARD", False),
        ("exact", "IfcBuildingElement", "subtypes", None, True),
        ("exact", "IfcWall", "exact", "STANDARD", True),
        ("subtypes", "IfcWall", "exact", "USERDEFINED", False),
        ("subtypes", "IfcDoor", "exact", None, False),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_source_union_constant_conflicts_consider_any_overlapping_pair(
    union_match,
    other_class,
    other_match,
    other_predefined,
    conflict,
    reverse,
):
    first = value_definition()
    first["source"] = union_source(
        {"schema": "IFC4", "ifc_class": "IfcSpace", "match": "exact"},
        {"schema": "IFC4", "ifc_class": "IfcWall", "match": union_match, "predefined_type": "STANDARD"},
    )
    second = value_definition(value={"kind": "constant", "value": False})
    second["source"] = {
        "schema": "IFC4",
        "ifc_class": other_class,
        "match": other_match,
        "predefined_type": other_predefined,
    }
    report = validate_translation_draft(profile(*([second, first] if reverse else [first, second])), Releases())
    assert any(d.code == "mapping_output_conflict" for d in report.diagnostics) is conflict
    assert report.authoring_valid is not conflict


@pytest.mark.parametrize("target_kind", ["class", "datatype_property", "object_property"])
@pytest.mark.parametrize("relationship", ["IfcRelAggregates", "IfcRelContainedInSpatialStructure", "IfcRelNests"])
def test_source_union_rejects_relationship_members_and_object_assertions(target_kind, relationship):
    invalid = value_definition() if target_kind == "datatype_property" else definition()
    invalid["source"] = union_source(
        {"schema": "IFC4", "ifc_class": "IfcWall", "match": "exact"},
        {"schema": "IFC4", "ifc_class": relationship, "match": "exact"},
    )
    if target_kind == "object_property":
        invalid["source"]["any_of"][0]["ifc_class"] = "IfcRelAggregates"
        invalid["subject"] = "relating"
        invalid["target"] = {"kind": target_kind, "iri": FBO + "Contains", "object": "related", "cardinality": "many"}
    report = validate_translation_draft(profile(invalid), Releases())
    assert not report.authoring_valid
    code = "mapping_relationship_unsupported" if target_kind == "object_property" else "mapping_subject_invalid"
    assert any(d.code == code for d in report.diagnostics)
