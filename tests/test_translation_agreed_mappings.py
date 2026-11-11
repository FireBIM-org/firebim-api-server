import copy

import pytest

from app.lexicon.ifc_mapping_evaluator import evaluate_ifc_mappings
from app.lexicon.translation_mapping_extraction import extract_module_mappings
from app.lexicon.translation_validation import (
    compile_translation_mappings,
    load_translation_modules,
    validate_translation_draft,
)
from app.schemas.translation import TranslationOntologyPin
from tests.test_translation_mapping_extraction import CORE, IFC, Releases, draft

FBO = "https://firebim.org/ontologies/fbo#"
DK = "https://firebim.org/ontologies/fbo/dk#"
XSD = "http://www.w3.org/2001/XMLSchema#"


def agreed_case(
    name, source, target="a owl:DatatypeProperty ; schema:rangeIncludes xsd:float", *, target_name=None, dk=""
):
    repository = Releases()
    target_name = target_name or name
    repository.add("core", CORE + f"\nfbo:{target_name} {target} .\n")
    repository.add("ifc", IFC + f"\nifc:{name} {source} ; ifc:mapsToTerm fbo:{target_name} .\n")
    profile = draft()
    if dk:
        repository.add(
            "dk",
            """
@prefix dk: <https://firebim.org/ontologies/fbo/dk#> .
@prefix fbo: <https://firebim.org/ontologies/fbo#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix schema: <http://schema.org/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
dk: a owl:Ontology ; owl:versionIRI <https://firebim.org/ontologies/fbo/dk/2026.09.04#> ; owl:imports fbo: .
"""
            + dk,
        )
        profile.contract.destination_modules.append(TranslationOntologyPin(family="fbo", country="dk"))
    profile.contract.mappings = [
        mapping
        for mapping in extract_module_mappings(profile, repository).mappings
        if mapping.seed_provenance["declaration_iri"].endswith("#" + name)
    ]
    return profile, repository


def compile_case(profile, repository):
    modules, _ = load_translation_modules(profile.contract, repository)
    return compile_translation_mappings(profile.contract, modules)


@pytest.mark.parametrize(
    "name,source,target_name,record,value,unit",
    [
        (
            "AreaFireCompartment2a",
            'ifc:hasClassPredefinedType "IfcSpace" ; '
            'ifc:hasPsetProperty "Qto_SpaceBaseQuantities.NetFloorArea" ; ifc:hasDataType "Area"',
            "AreaFireCompartment",
            {"id": "s", "ifc_class": "IfcSpace", "quantities": {"Qto_SpaceBaseQuantities": {"NetFloorArea": 32.5}}},
            32.5,
            "area",
        ),
        (
            "HeightBuilding",
            'ifc:hasClassPredefinedType "IfcBuilding" ; '
            'ifc:hasPsetProperty "CPset_FireBIM.HeightBuilding" ; ifc:hasDataType "Length"',
            "HeightBuilding",
            {"id": "b", "ifc_class": "IfcBuilding", "properties": {"CPset_FireBIM": {"HeightBuilding": 12.5}}},
            12.5,
            "length",
        ),
    ],
)
def test_approved_supplied_measurements_compile_without_geometry_or_snapshot_changes(
    name, source, target_name, record, value, unit
):
    profile, repository = agreed_case(name, source, target_name=target_name)
    before = copy.deepcopy(profile.model_dump(mode="json"))
    report = validate_translation_draft(profile, repository)
    assert report.authoring_valid, report.diagnostics
    compiled, diagnostics = compile_case(profile, repository)
    assert not [issue for issue in diagnostics if issue.severity == "error"]
    result = evaluate_ifc_mappings(compiled, [record], "pilot")
    assert not result.diagnostics
    assert len(result.assertions) == 1
    assert result.assertions[0].object == value
    assert result.assertions[0].unit.dimension == unit
    assert result.assertions[0].unit.scale_to_si == 1
    assert not evaluate_ifc_mappings(
        compiled,
        [{"id": "missing", "ifc_class": record["ifc_class"], "geometry": {"area": 100, "height": 100}}],
        "pilot",
    ).assertions
    assert profile.model_dump(mode="json") == before


def test_area_prefers_custom_value_and_reports_disagreement_with_quantity():
    profile, repository = agreed_case(
        "AreaFireCompartment1",
        'ifc:hasClassPredefinedType "IfcSpatialZone.FIRESAFETY" ; '
        'ifc:hasPsetProperty "Qto_SpatialZoneOtherBaseQuantities.NetFloorArea, CPset_FireBIM.CompartmentArea" ; '
        'ifc:hasDataType "Area"',
        target_name="AreaFireCompartment",
    )
    compiled, diagnostics = compile_case(profile, repository)
    assert compiled, diagnostics
    record = {
        "id": "z",
        "ifc_class": "IfcSpatialZone",
        "predefined_type": "FIRESAFETY",
        "properties": {"CPset_FireBIM": {"CompartmentArea": 20.5}},
        "quantities": {"Qto_SpatialZoneOtherBaseQuantities": {"NetFloorArea": 19.5}},
    }
    result = evaluate_ifc_mappings(compiled, [record], "pilot")
    assert [assertion.object for assertion in result.assertions] == [20.5]
    assert any(
        issue.code == "mapping_source_disagreement" and issue.severity == "warning" for issue in result.diagnostics
    )
    del record["properties"]
    assert [assertion.object for assertion in evaluate_ifc_mappings(compiled, [record], "pilot").assertions] == [19.5]


def test_teaching_zone_uses_approved_exact_values_and_does_not_invent_false():
    profile, repository = agreed_case(
        "TeachingZone",
        'ifc:hasClassPredefinedType "IfcSpace" ; '
        'ifc:hasPsetProperty "Pset_SpaceOccupancyRequirements.OccupancyType" ; '
        'ifc:hasDataType "String" ; ifc:hasValue "EDUCATION, TEACHING"',
        "a owl:DatatypeProperty ; schema:rangeIncludes xsd:boolean",
    )
    compiled, diagnostics = compile_case(profile, repository)
    assert compiled, diagnostics
    records = [
        {
            "id": str(index),
            "ifc_class": "IfcSpace",
            "properties": {"Pset_SpaceOccupancyRequirements": {"OccupancyType": value}},
        }
        for index, value in enumerate(["EDUCATION", "TEACHING", "education", "OTHER", None, "EDUCATION, TEACHING"])
    ]
    result = evaluate_ifc_mappings(compiled, records, "pilot")
    assert not result.diagnostics
    assert {assertion.subject.rsplit(":", 1)[-1] for assertion in result.assertions} == {"0", "1"}
    assert all(assertion.object is True for assertion in result.assertions)


def test_named_compartments_group_spaces_instead_of_classifying_each_space():
    profile, repository = agreed_case(
        "FireCompartment2b",
        'ifc:hasClassPredefinedType "IfcSpace" ; ifc:hasPsetProperty "CPset_FireBIM.Compartment" ; '
        'ifc:hasDataType "String" ; ifc:hasValue "<name of compartment>"',
        "a owl:Class",
        target_name="FireCompartment",
    )
    compiled, diagnostics = compile_case(profile, repository)
    assert compiled, diagnostics
    result = evaluate_ifc_mappings(
        compiled,
        [
            {"id": str(index), "ifc_class": "IfcSpace", "properties": {"CPset_FireBIM": {"Compartment": name}}}
            for index, name in enumerate(["A", "A", "B", "", None])
        ],
        "pilot",
    )
    assert not result.diagnostics
    assert len(result.assertions) == 2
    assert len(result.memberships) == 3
    assert len({membership.group for membership in result.memberships}) == 2
    assert not any(
        assertion.subject in {membership.member for membership in result.memberships} for assertion in result.assertions
    )


@pytest.mark.parametrize("name", ["EscapeRoute", "FireCompartment2a", "FireCompartment2c", "FireResistance"])
def test_agreed_skips_are_visible_exclusions_not_successful_compilation(name):
    profile, repository = agreed_case(name, 'ifc:hasClassPredefinedType "IfcSpace"', "a owl:Class")
    profile.contract.mappings.extend(
        mapping
        for mapping in extract_module_mappings(draft(), repository).mappings
        if mapping.seed_provenance["declaration_iri"].endswith("#Building")
    )
    before = profile.model_dump(mode="json")
    compiled, diagnostics = compile_case(profile, repository)
    assert len(compiled) == 1
    assert any(
        issue.code == "mapping_excluded_by_agreement"
        and issue.mapping_id == profile.contract.mappings[0].id
        and issue.severity == "info"
        for issue in diagnostics
    )
    assert validate_translation_draft(profile, repository).authoring_valid
    assert profile.model_dump(mode="json") == before


def test_skipped_only_set_is_not_a_valid_executable_profile():
    profile, repository = agreed_case("EscapeRoute", 'ifc:hasClassPredefinedType "IfcSpace"', "a owl:Class")
    report = validate_translation_draft(profile, repository)
    assert not report.authoring_valid
    assert any(issue.code == "executable_mappings_required" for issue in report.diagnostics)


def test_skip_policy_does_not_bypass_provenance_checks():
    profile, repository = agreed_case("EscapeRoute", 'ifc:hasClassPredefinedType "IfcSpace"', "a owl:Class")
    profile.contract.mappings[0].definition["source"]["class_predefined_types"] = ["IfcDoor"]
    _, diagnostics = compile_case(profile, repository)
    assert any(issue.code == "mapping_seed_modified" for issue in diagnostics)


def test_storey_count_remains_blocked_until_class_declaration_is_corrected():
    profile, repository = agreed_case(
        "BuildingLayersBuilding",
        'ifc:hasClassPredefinedType "IfcBuilding" ; ifc:hasPsetProperty "Pset_BuildingCommon.NumberOfStoreys" ; '
        'ifc:hasDataType "Integer"',
        "a owl:Class",
        target_name="BuildingLayers",
    )
    compiled, diagnostics = compile_case(profile, repository)
    assert not compiled
    assert any(issue.code == "mapping_ontology_correction_required" for issue in diagnostics)


def test_generic_extinguishing_flag_is_not_interpreted_as_sprinkler_evidence():
    profile, repository = agreed_case(
        "AutomaticFireExtinguishSystem",
        'ifc:hasClassPredefinedType "IfcSpace, IfcSpatialZone.FIRESAFETY, IfcZone" ; '
        'ifc:hasPsetProperty "CPset_FireBIM.AutomaticFireExtinguishSystem" ; ifc:hasDataType "Boolean" ; '
        'ifc:hasInterpretationResult "Is this targeting sprinklers '
        'or is this more generic and would others also apply?"',
        "a owl:DatatypeProperty ; schema:rangeIncludes xsd:boolean",
    )
    compiled, diagnostics = compile_case(profile, repository)
    assert compiled, diagnostics
    result = evaluate_ifc_mappings(
        compiled,
        [
            {
                "id": "s",
                "ifc_class": "IfcSpace",
                "properties": {"CPset_FireBIM": {"AutomaticFireExtinguishSystem": True}},
            }
        ],
        "pilot",
    )
    assert len(result.assertions) == 1
    assert result.assertions[0].predicate == FBO + "AutomaticFireExtinguishSystem"


def test_approval_does_not_apply_to_an_unrecognized_measurement_or_changed_selector():
    profile, repository = agreed_case(
        "HeightBuilding",
        'ifc:hasClassPredefinedType "IfcBuilding" ; ifc:hasPsetProperty "CPset_Other.HeightBuilding" ; '
        'ifc:hasDataType "Length"',
    )
    assert not validate_translation_draft(profile, repository).authoring_valid


def test_final_dk_occupant_target_replaces_core_object_property_without_rewriting_it():
    profile, repository = agreed_case(
        "OccupantsCompartment",
        'ifc:hasClassPredefinedType "IfcSpace, IfcSpatialZone.FIRESAFETY, IfcZone" ; '
        'ifc:hasPsetProperty "Pset_SpaceOccupancyRequirements.OccupancyNumber" ; ifc:hasDataType "Integer"',
        "a owl:ObjectProperty",
        target_name="NumberOccupants",
        dk="dk:NumberOccupantsSpaceCompartment a owl:DatatypeProperty ; schema:rangeIncludes xsd:float ; "
        "fbo:hasIFCMapping <https://firebim.org/ontologies/fbo/ifc#OccupantsCompartment> .",
    )
    before = profile.model_dump(mode="json")
    compiled, diagnostics = compile_case(profile, repository)
    assert len(compiled) == 2, diagnostics
    assert all(mapping.target.iri == DK + "NumberOccupantsSpaceCompartment" for mapping in compiled.values())
    assert all(mapping.target.datatype == XSD + "float" for mapping in compiled.values())
    result = evaluate_ifc_mappings(
        compiled,
        [
            {
                "id": "s",
                "ifc_class": "IfcSpace",
                "properties": {"Pset_SpaceOccupancyRequirements": {"OccupancyNumber": 12}},
            }
        ],
        "pilot",
    )
    assert not result.diagnostics
    assert len(result.assertions) == 1 and result.assertions[0].object == 12
    assert profile.model_dump(mode="json") == before


def test_final_dk_width_uses_declared_float_and_only_explicit_fire_exit_doors():
    profile, repository = agreed_case(
        "WidthFreeDoors",
        'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "Qto_DoorBaseQuantities.Width" ; '
        'ifc:hasDataType "Length" ; '
        'ifc:hasArgumentation "To be combined with the identification of escape door or fire exits"',
        "a owl:DatatypeProperty",
        dk="dk:WidthFreeDoors a owl:DatatypeProperty ; schema:rangeIncludes xsd:float ; "
        "rdfs:subPropertyOf fbo:WidthFreeDoors .",
    )
    compiled, diagnostics = compile_case(profile, repository)
    assert len(compiled) == 1, diagnostics
    assert next(iter(compiled.values())).target.iri == DK + "WidthFreeDoors"
    result = evaluate_ifc_mappings(
        compiled,
        [
            {
                "id": str(index),
                "ifc_class": "IfcDoor",
                "quantities": {"Qto_DoorBaseQuantities": {"Width": 900}},
                "properties": {"Pset_DoorCommon": {"FireExit": flag}},
            }
            for index, flag in enumerate([True, False, None])
        ],
        "pilot",
        model_units={"length": {"symbol": "mm", "dimension": "length", "scale_to_si": 0.001}},
    )
    assert not result.diagnostics
    assert len(result.assertions) == 1 and result.assertions[0].object == 900
    assert result.assertions[0].unit.symbol == "mm"


def test_final_target_is_independent_of_selection_order_and_missing_final_range_is_not_invented():
    profile, repository = agreed_case(
        "WidthFreeDoors",
        'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "Qto_DoorBaseQuantities.Width" ; '
        'ifc:hasDataType "Length"',
        "a owl:DatatypeProperty",
        dk="dk:WidthFreeDoors a owl:DatatypeProperty ; schema:rangeIncludes xsd:float ; "
        "rdfs:subPropertyOf fbo:WidthFreeDoors .",
    )
    compiled, _ = compile_case(profile, repository)
    profile.contract.destination_modules.reverse()
    assert compile_case(profile, repository)[0] == compiled
    repository.add("dk", repository.items["dk"].ttl.replace("schema:rangeIncludes xsd:float ;", ""))
    assert not validate_translation_draft(profile, repository).authoring_valid


def test_changed_approved_annotation_still_requires_a_decision():
    profile, repository = agreed_case(
        "FireCompartment2b",
        'ifc:hasClassPredefinedType "IfcSpace" ; ifc:hasPsetProperty "CPset_FireBIM.Compartment" ; '
        'ifc:hasDataType "String" ; ifc:hasValue "<name of compartment>" ; '
        'ifc:hasInterpretationResult "Group across all uploaded models"',
        "a owl:Class",
        target_name="FireCompartment",
    )
    _, diagnostics = compile_case(profile, repository)
    assert any(issue.code == "mapping_interpretation_required" for issue in diagnostics)


@pytest.mark.parametrize(
    "name,target", [("ActiveFireProtection", "a owl:ObjectProperty"), ("AutomaticDetection", "a owl:Class")]
)
def test_unresolved_system_entities_are_not_invented_or_combined(name, target):
    profile, repository = agreed_case(name, 'ifc:hasClassPredefinedType "IfcSpace"', target)
    compiled, diagnostics = compile_case(profile, repository)
    assert not compiled
    assert any(issue.code == "mapping_system_definition_required" for issue in diagnostics)
