import copy

import pytest

from app.lexicon.ifc_mapping_evaluator import evaluate_ifc_mappings
from app.lexicon.translation_mapping_extraction import extract_module_mappings
from app.lexicon.translation_validation import (
    compile_translation_mappings,
    load_translation_modules,
    validate_translation_draft,
)
from tests.test_translation_mapping_extraction import CORE, IFC, Releases, by_target, draft, module_case

BUILDING_NOTE = (
    "One or more buildings can exist in each IFC file (although typically not supported by modelling software)"
)
COMPARTMENT_NOTE = "This seems (in general) the preferred entity in IFC to capture a compartment of any kind."


def annotated_profile(building_note=BUILDING_NOTE, compartment_note=COMPARTMENT_NOTE):
    repository = Releases()
    repository.add("core", CORE + "\nfbo:FireCompartment a owl:Class .\n")
    repository.add(
        "ifc",
        IFC
        + f'''
ifc:Building ifc:hasInterpretationResult "{building_note}" .
ifc:FireCompartment1 ifc:hasClassPredefinedType "IfcSpatialZone.FIRESAFETY" ;
    ifc:hasArgumentation "{compartment_note}" ; ifc:hasInterpretationResult "Modelled compartment" ;
    ifc:mapsToTerm fbo:FireCompartment .
''',
    )
    profile = draft()
    profile.contract.mappings = [
        mapping
        for mapping in extract_module_mappings(profile, repository).mappings
        if mapping.definition["target"]["iri"].endswith(("#Building", "#FireCompartment"))
    ]
    return profile, repository


def test_explanatory_release_annotations_compile_and_evaluate_without_mutating_saved_snapshots():
    profile, repository = annotated_profile()
    before = copy.deepcopy(profile.model_dump(mode="json"))
    report = validate_translation_draft(profile, repository)
    assert report.authoring_valid
    assert report.publish_ready and not report.runtime_available
    assert sum(item.code == "mapping_annotation_preserved" for item in report.diagnostics) == 3
    modules, _ = load_translation_modules(profile.contract, repository)
    compiled, diagnostics = compile_translation_mappings(profile.contract, modules)
    assert not [item for item in diagnostics if item.severity == "error"]
    result = evaluate_ifc_mappings(
        compiled,
        [
            {"id": "building-1", "ifc_class": "IfcBuilding"},
            {"id": "building-2", "ifc_class": "IfcBuilding"},
            {"id": "fire", "ifc_class": "IfcSpatialZone", "predefined_type": "FIRESAFETY"},
            {"id": "thermal", "ifc_class": "IfcSpatialZone", "predefined_type": "THERMAL"},
        ],
        "fixture",
    )
    assert not result.diagnostics
    assert {item.subject.rsplit(":", 1)[-1] for item in result.assertions} == {"building-1", "building-2", "fire"}
    assert profile.model_dump(mode="json") == before


@pytest.mark.parametrize("change", ["new_question", "extra_note", "language", "datatype", "other_declaration"])
def test_annotation_review_is_exact_and_unknown_notes_do_not_inherit_approval(change):
    profile, repository = annotated_profile()
    text = repository.items["ifc"].ttl
    if change == "new_question":
        text = text.replace(BUILDING_NOTE, "Should only the primary building be included?")
    elif change == "extra_note":
        text += '\nifc:Building ifc:hasArgumentation "Should buildings be merged?" .'
    elif change == "language":
        text = text.replace(f'"{BUILDING_NOTE}"', f'"{BUILDING_NOTE}"@en')
    elif change == "datatype":
        text = text.replace(f'"{BUILDING_NOTE}"', f'"{BUILDING_NOTE}"^^<https://example.org/Note>')
    else:
        text = text.replace("ifc:Building ", "ifc:OtherBuilding ")
    repository.add("ifc", text)
    profile.contract.mappings = [
        mapping
        for mapping in extract_module_mappings(draft(), repository).mappings
        if mapping.definition["target"]["iri"].endswith("#Building")
    ]
    report = validate_translation_draft(profile, repository)
    assert not report.authoring_valid
    error = next(item for item in report.diagnostics if item.code == "mapping_interpretation_required")
    assert "Decision needed" in error.message
    assert "Building" in error.message


@pytest.mark.parametrize(
    "source,target,code,question",
    [
        (
            'ifc:hasClassPredefinedType "IfcSpatialElement" ; '
            'ifc:hasPsetProperty "Pset_Test.A, Pset_Test.B" ; ifc:hasDataType "Boolean"',
            "a owl:ObjectProperty ; schema:rangeIncludes xsd:boolean",
            "mapping_selector_unsupported",
            "combination",
        ),
        (
            'ifc:hasClassPredefinedType "IfcSpace" ; ifc:hasPsetProperty "Qto_SpaceBaseQuantities.NetFloorArea" ; '
            'ifc:hasDataType "Area"',
            "a owl:DatatypeProperty ; schema:rangeIncludes xsd:float",
            "mapping_units_required",
            "unit",
        ),
        (
            'ifc:hasClassPredefinedType "IfcBuilding" ; ifc:hasPsetProperty "CPset_FireBIM.HeightBuilding" ; '
            'ifc:hasDataType "Length"',
            "a owl:DatatypeProperty ; schema:rangeIncludes xsd:float",
            "mapping_units_required",
            "measurement",
        ),
        (
            'ifc:hasClassPredefinedType "IfcSpace" ; ifc:hasPsetProperty "CPset_Test.Detection" ; '
            'ifc:hasDataType "Boolean"',
            "a owl:Class",
            "mapping_condition_required",
            "class",
        ),
        (
            'ifc:hasClassPredefinedType "IfcSpace"',
            "a owl:DatatypeProperty ; schema:rangeIncludes xsd:boolean",
            "mapping_selector_unsupported",
            "qualif",
        ),
        (
            'ifc:hasClassPredefinedType "IfcSpace" ; ifc:hasPsetProperty "ElementType" ; '
            'ifc:hasDataType "String" ; ifc:hasValue "COMPARTMENT"',
            "a owl:Class",
            "mapping_attribute_unsupported",
            "ElementType",
        ),
        (
            'ifc:hasClassPredefinedType "IfcSpace" ; ifc:hasPsetProperty "CPset_Test.Compartment" ; '
            'ifc:hasDataType "String" ; ifc:hasValue "<name of compartment>"',
            "a owl:Class",
            "mapping_interpretation_required",
            "classification",
        ),
        (
            'ifc:hasClassPredefinedType "IfcSpace" ; ifc:hasPsetProperty "Pset_Test.Count" ; ifc:hasDataType "Integer"',
            "a owl:ObjectProperty",
            "mapping_relationship_unsupported",
            "datatype",
        ),
        (
            'ifc:hasClassPredefinedType "IfcSpace" ; ifc:hasPsetProperty "Pset_Test.Use" ; '
            'ifc:hasDataType "String" ; ifc:hasValue "EDUCATION, TEACHING"',
            "a owl:DatatypeProperty ; schema:rangeIncludes xsd:boolean",
            "mapping_source_datatype_mismatch",
            "Boolean",
        ),
        (
            'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "Qto_DoorBaseQuantities.Width" ; '
            'ifc:hasDataType "Length"',
            "a owl:DatatypeProperty",
            "mapping_target_range_unresolved",
            "range",
        ),
    ],
)
def test_incomplete_module_semantics_report_a_specific_decision(source, target, code, question):
    profile, repository = module_case(source, target)
    report = validate_translation_draft(profile, repository)
    assert not report.authoring_valid
    issue = next(item for item in report.diagnostics if item.code == code)
    assert issue.mapping_id == profile.contract.mappings[0].id
    assert "Decision needed" in issue.message
    assert "Case" in issue.message and question in issue.message


def test_unknown_annotation_includes_the_actual_unresolved_question_and_target():
    profile, repository = module_case(
        'ifc:hasClassPredefinedType "IfcSpace" ; '
        'ifc:hasInterpretationResult "Are these concepts equivalent across countries?"'
    )
    issue = next(
        item
        for item in validate_translation_draft(profile, repository).diagnostics
        if item.code == "mapping_interpretation_required"
    )
    assert "Are these concepts equivalent across countries?" in issue.message
    assert "https://firebim.org/ontologies/fbo#Case" in issue.message


def test_supported_mapping_cannot_hide_an_unknown_semantic_predicate_behind_an_explanatory_note():
    profile, repository = annotated_profile()
    repository.add("ifc", repository.items["ifc"].ttl + "\nifc:Building ifc:mergeBuildings true .")
    profile.contract.mappings = [
        by_target(extract_module_mappings(draft(), repository))["https://firebim.org/ontologies/fbo#Building"]
    ]
    report = validate_translation_draft(profile, repository)
    assert not report.authoring_valid
    assert any(item.code == "mapping_module_declaration_unsupported" for item in report.diagnostics)
