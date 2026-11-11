import hashlib
from types import SimpleNamespace

import pytest
from rdflib import Graph

from app.lexicon.ifc_mapping_evaluator import evaluate_ifc_mappings
from app.lexicon.translation_mapping_extraction import extract_module_mappings
from app.lexicon.translation_validation import (
    compile_translation_mappings,
    load_translation_modules,
    validate_translation_draft,
)
from app.schemas.translation import TranslationContract, TranslationStoredProfile
from app.storage.graph_store import GraphStoreError, GraphStoreNotFoundError

CORE = """
@prefix fbo: <https://firebim.org/ontologies/fbo#> .
@prefix ifc: <https://firebim.org/ontologies/fbo/ifc#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix schema: <http://schema.org/> .
fbo: a owl:Ontology ; owl:versionIRI <https://firebim.org/ontologies/fbo/2026.09.04#> .
fbo:Building a owl:Class ; fbo:hasIFCMapping ifc:Building .
fbo:ExteriorWall a owl:DatatypeProperty ; schema:rangeIncludes xsd:boolean ;
    fbo:hasIFCMapping ifc:ExteriorWall .
fbo:FireResistance a owl:DatatypeProperty ; schema:rangeIncludes xsd:float ;
    fbo:hasIFCMapping ifc:FireResistance .
"""

IFC = """
@prefix fbo: <https://firebim.org/ontologies/fbo#> .
@prefix ifc: <https://firebim.org/ontologies/fbo/ifc#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ifc: a owl:Ontology ; owl:versionIRI <https://firebim.org/ontologies/fbo/ifc/2026.09.04#> ;
    owl:imports fbo: ; rdfs:label "FBO → IFC 4.3 mapping" .
ifc:Building ifc:hasClassPredefinedType "IfcBuilding" ; ifc:hasKind "Object" ;
    ifc:mapsToTerm fbo:Building, <https://example.org/unselected#Building> .
ifc:ExteriorWall ifc:hasClassPredefinedType "IfcWall" ; ifc:hasKind "Object" ;
    ifc:hasPsetProperty "Pset_WallCommon.IsExternal" ; ifc:hasDataType "Boolean" ;
    ifc:hasValue "True" ; ifc:mapsToTerm fbo:ExteriorWall .
ifc:FireResistance ifc:hasClassPredefinedType "IfcWall, IfcDoor" ;
    ifc:hasPsetProperty "Pset_*Common.FireRating" ; ifc:hasDataType "String" ;
    ifc:hasValue "REI60", "<REI value string>" ;
    ifc:hasInterpretationResult "Rating criteria and units unresolved"@en ;
    ifc:mapsToTerm fbo:FireResistance .
"""


class Releases:
    def __init__(self):
        self.items = {}
        self.calls = []
        self.add("core", CORE)
        self.add("ifc", IFC)

    def add(self, module, ttl):
        iri = "https://firebim.org/ontologies/fbo" + (f"/{module}" if module != "core" else "")
        self.items[module] = SimpleNamespace(
            ttl=ttl,
            version=SimpleNamespace(
                version="2026.09.04",
                ontology_uri=f"{iri}#",
                version_iri=f"{iri}/2026.09.04#",
                sha256=hashlib.sha256(ttl.encode()).hexdigest(),
            ),
        )

    def fetch_version(self, family, country, version):
        self.calls.append((family, country, version))
        assert family == "fbo" and version == "2026.09.04"
        item = self.items.get(country, GraphStoreNotFoundError("missing release"))
        if isinstance(item, Exception):
            raise item
        return item


def draft(**changes):
    contract = TranslationContract(
        destination_modules=[
            {"family": "fbo", "country": "core"},
            {"family": "fbo", "country": "ifc"},
        ],
        **changes,
    )
    return TranslationStoredProfile(id="ifct_test", name="pilot", contract=contract, created_at="now", updated_at="now")


def by_target(report):
    return {mapping.definition["target"]["iri"]: mapping for mapping in report.mappings}


def test_extracts_forward_and_inverse_links_once_with_exact_iri_identity():
    repository = Releases()
    profile = draft()
    before = profile.model_dump(mode="json")
    report = extract_module_mappings(profile, repository)
    assert report.profile_id == profile.id and report.generation == 1
    assert len(report.mappings) == 3
    assert len({mapping.id for mapping in report.mappings}) == 3
    assert all(mapping.enabled and mapping.review_state == "accepted" for mapping in report.mappings)
    assert report.skipped_existing == 0
    assert repository.calls == [("fbo", "core", "2026.09.04"), ("fbo", "ifc", "2026.09.04")]
    assert profile.model_dump(mode="json") == before
    assert any(d.code == "mapping_target_not_selected" for d in report.diagnostics)
    assert by_target(report)["https://firebim.org/ontologies/fbo#Building"].definition["target"]["kind"] == "class"


def test_destination_declaration_overrides_seed_category_without_compiling_boolean():
    repository = Releases()
    mapping = by_target(extract_module_mappings(draft(), repository))["https://firebim.org/ontologies/fbo#ExteriorWall"]
    definition = mapping.definition
    assert definition["language"] == "fbo-module-suggestion/1"
    assert definition["target"] == {
        "iri": "https://firebim.org/ontologies/fbo#ExteriorWall",
        "kind": "datatype_property",
        "ranges": ["http://www.w3.org/2001/XMLSchema#boolean"],
    }
    assert definition["source"]["values"] == ["True"]
    assert definition["source"]["pset_properties"] == ["Pset_WallCommon.IsExternal"]
    assert definition["source"]["schema"] == "IFC4"
    assert mapping.enabled and mapping.review_state == "accepted"
    assert "source_schema_compatibility_unverified" in definition["review_issues"]
    provenance = mapping.seed_provenance
    assert provenance["kind"] == "ontology_module"
    assert provenance["module"]["sha256"] == repository.items["ifc"].version.sha256
    assert provenance["module"]["version"] == "2026.09.04"
    assert "IFC 4.3" in str(provenance["source_schema_evidence"])


def test_reviewed_supported_module_declarations_compile_and_validate_without_reauthoring():
    repository = Releases()
    profile = draft()
    profile.contract.mappings = extract_module_mappings(profile, repository).mappings
    for mapping in profile.contract.mappings:
        if mapping.definition["target"]["iri"].endswith("#FireResistance"):
            mapping.enabled = False
    before = profile.model_dump(mode="json")
    report = validate_translation_draft(profile, repository)
    assert report.authoring_valid
    assert report.publish_ready and not report.runtime_available
    assert sum(d.code == "module_mapping_compiled" for d in report.diagnostics) == 2
    assert profile.model_dump(mode="json") == before
    modules, _ = load_translation_modules(profile.contract, repository)
    compiled, diagnostics = compile_translation_mappings(profile.contract, modules)
    assert not any(d.severity == "error" for d in diagnostics)
    exterior = next(mapping for mapping in compiled.values() if mapping.target.iri.endswith("#ExteriorWall"))
    assert exterior.target.kind == "datatype_property"
    assert exterior.target.datatype == "http://www.w3.org/2001/XMLSchema#boolean"
    assert exterior.condition.values == [True]
    assert exterior.target.value.selector.pset == "Pset_WallCommon"
    assert exterior.source.match == "exact"
    evaluated = evaluate_ifc_mappings(
        compiled,
        [
            {"id": "building", "ifc_class": "IfcBuilding"},
            {"id": "external", "ifc_class": "IfcWall", "properties": {"Pset_WallCommon": {"IsExternal": True}}},
            {"id": "internal", "ifc_class": "IfcWall", "properties": {"Pset_WallCommon": {"IsExternal": False}}},
            {"id": "missing", "ifc_class": "IfcWall"},
        ],
        "fixture",
    )
    assert not evaluated.diagnostics
    assert len(evaluated.assertions) == 2
    boolean = next(item for item in evaluated.assertions if item.datatype is not None)
    assert boolean.subject == "urn:firebim:model:fixture:entity:external"
    assert boolean.predicate == "https://firebim.org/ontologies/fbo#ExteriorWall"
    assert boolean.object is True
    assert boolean.evidence[0].source_id == "external"


def test_default_enabled_mappings_still_block_unresolved_fire_resistance():
    # Agreed skips now block execution of this declaration, not unrelated authoring.
    repository = Releases()
    profile = draft()
    profile.contract.mappings = extract_module_mappings(profile, repository).mappings
    before = profile.model_dump(mode="json")
    report = validate_translation_draft(profile, repository)
    assert report.authoring_valid and report.publish_ready and not report.runtime_available
    fire_mapping = next(
        mapping
        for mapping in profile.contract.mappings
        if mapping.definition["target"]["iri"].endswith("#FireResistance")
    )
    assert fire_mapping.enabled
    assert any(
        d.code == "mapping_excluded_by_agreement" and d.mapping_id == fire_mapping.id for d in report.diagnostics
    )
    modules, _ = load_translation_modules(profile.contract, repository)
    compiled, _ = compile_translation_mappings(profile.contract, modules)
    assert len(compiled) == 2 and fire_mapping.id not in compiled
    assert not evaluate_ifc_mappings(
        compiled,
        [{"id": "wall", "ifc_class": "IfcWall", "properties": {"Pset_WallCommon": {"FireRating": "REI60"}}}],
        "fixture",
    ).assertions
    assert profile.model_dump(mode="json") == before
    assert not any(d.code == "mapping_review_required" for d in report.diagnostics)


def test_legacy_disabled_imports_are_preserved_and_validation_explains_how_to_enable_them():
    repository = Releases()
    profile = draft()
    profile.generation = 12
    profile.contract.mappings = [
        mapping.model_copy(
            update={
                "enabled": False,
                "review_state": "unresolved",
            }
        )
        for mapping in extract_module_mappings(profile, repository).mappings
    ]
    before = profile.model_dump(mode="json")

    extraction = extract_module_mappings(profile, repository)
    assert extraction.mappings == [] and extraction.skipped_existing == 3
    report = validate_translation_draft(profile, repository)
    assert report.generation == 12 and not report.authoring_valid
    assert next(step.status for step in report.steps if step.id == "destination_ontologies") == "passed"
    errors = [diagnostic for diagnostic in report.diagnostics if diagnostic.severity == "error"]
    assert [diagnostic.code for diagnostic in errors] == ["enabled_mappings_required"]
    assert profile.model_dump(mode="json") == before
    assert "3 saved mappings are disabled" in errors[0].message
    assert "Enable all module mappings" in errors[0].message
    assert "Save draft" in errors[0].message


@pytest.mark.parametrize(
    "mappings, expected",
    [
        ([], "No mappings are saved."),
        ([{"id": "manual", "enabled": False, "definition": {}}], "1 saved mapping is disabled."),
    ],
)
def test_no_enabled_mapping_diagnostic_distinguishes_empty_and_manual_drafts(mappings, expected):
    profile = draft(mappings=mappings)
    report = validate_translation_draft(profile, Releases())
    error = next(diagnostic for diagnostic in report.diagnostics if diagnostic.code == "enabled_mappings_required")
    assert expected in error.message
    assert "Enable all module mappings" not in error.message
    assert "Save draft" in error.message
    assert not report.authoring_valid


def test_stored_module_buildingsmart_annotation_is_provenance_not_an_extra_selector():
    repository = Releases()
    repository.add(
        "ifc",
        IFC
        + """
ifc:ExteriorWall a owl:NamedIndividual ; rdfs:label "ExteriorWall"@en ; ifc:isBuildingSMART true .
""",
    )
    profile = draft()
    mapping = by_target(extract_module_mappings(profile, repository))["https://firebim.org/ontologies/fbo#ExteriorWall"]
    mapping.enabled, mapping.review_state = True, "accepted"
    profile.contract.mappings = [mapping]
    assert validate_translation_draft(profile, repository).authoring_valid


def module_case(source, target="a owl:Class", extra=""):
    repository = Releases()
    repository.add("core", CORE + f"\nfbo:Case {target} .\n")
    repository.add("ifc", IFC + f"\nifc:Case {source} ; ifc:mapsToTerm fbo:Case .\n" + extra)
    profile = draft()
    profile.contract.mappings = [
        by_target(extract_module_mappings(profile, repository))["https://firebim.org/ontologies/fbo#Case"]
    ]
    return profile, repository


@pytest.mark.parametrize(
    "datatype, literal, selected, present, absent",
    [
        ("Boolean", '"True"', "Pset_DoorCommon.FireExit", True, False),
        ("Boolean", "false", "Pset_DoorCommon.FireExit", False, True),
        ("String", '"FireCompartment"', "ObjectType", "FireCompartment", "Other"),
        ("String", '"EDUCATION, TEACHING"', "CPset_FireBIM.Use", "EDUCATION, TEACHING", "EDUCATION"),
    ],
)
def test_module_conditional_classes_preserve_typed_conditions_and_evidence(
    datatype,
    literal,
    selected,
    present,
    absent,
):
    profile, repository = module_case(
        f'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "{selected}" ; '
        f'ifc:hasDataType "{datatype}" ; ifc:hasValue {literal}'
    )
    before = profile.model_dump(mode="json")
    modules, _ = load_translation_modules(profile.contract, repository)
    compiled, diagnostics = compile_translation_mappings(profile.contract, modules)
    assert not [item for item in diagnostics if item.severity == "error"]
    assert validate_translation_draft(profile, repository).authoring_valid
    executable = next(iter(compiled.values()))
    assert executable.target.kind == "class"
    assert executable.condition.values == [present]

    def record(identifier, value):
        fields = (
            {"attributes": {selected: value}}
            if "." not in selected
            else {"properties": {selected.split(".")[0]: {selected.split(".")[1]: value}}}
        )
        return {"id": identifier, "ifc_class": "IfcDoor", **fields}

    result = evaluate_ifc_mappings(
        compiled,
        [
            record("yes", present),
            record("no", absent),
            {"id": "missing", "ifc_class": "IfcDoor"},
        ],
        "fixture",
    )
    assert not result.diagnostics
    assert len(result.assertions) == 1
    assert result.assertions[0].subject.endswith(":yes")
    assert result.assertions[0].object.endswith("#Case")
    assert any(item.name == selected.split(".")[-1] for item in result.assertions[0].evidence)
    assert profile.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "classes",
    [
        '"IfcSpace, IfcSpatialZone.FIRESAFETY, IfcZone"',
        '"IfcZone", "IfcSpace", "IfcSpatialZone.FIRESAFETY"',
    ],
)
def test_module_class_union_is_bounded_exact_and_preserves_original_mapping_identity(classes):
    profile, repository = module_case(f"ifc:hasClassPredefinedType {classes}")
    before = profile.model_dump(mode="json")
    modules, _ = load_translation_modules(profile.contract, repository)
    compiled, diagnostics = compile_translation_mappings(profile.contract, modules)
    assert not [item for item in diagnostics if item.severity == "error"]
    assert list(compiled) == [profile.contract.mappings[0].id]
    records = [
        {"id": "space", "ifc_class": "IfcSpace"},
        {"id": "zone", "ifc_class": "IfcZone"},
        {"id": "fire", "ifc_class": "IfcSpatialZone", "predefined_type": "FIRESAFETY"},
        {"id": "other", "ifc_class": "IfcSpatialZone", "predefined_type": "THERMAL"},
        {"id": "missing", "ifc_class": "IfcSpatialZone"},
        {"id": "door", "ifc_class": "IfcDoor"},
    ]
    result = evaluate_ifc_mappings(compiled, records, "fixture")
    assert not result.diagnostics
    assert {item.subject.rsplit(":", 1)[-1] for item in result.assertions} == {"space", "zone", "fire"}
    assert all(item.evidence[0].mapping_id == profile.contract.mappings[0].id for item in result.assertions)
    assert profile.model_dump(mode="json") == before


def test_module_integer_count_union_uses_checked_cast_without_string_parsing():
    profile, repository = module_case(
        'ifc:hasClassPredefinedType "IfcSpace, IfcSpatialZone.FIRESAFETY, IfcZone" ; '
        'ifc:hasPsetProperty "Pset_SpaceOccupancyRequirements.OccupancyNumber" ; ifc:hasDataType "Integer"',
        "a owl:DatatypeProperty ; schema:rangeIncludes xsd:float",
    )
    modules, _ = load_translation_modules(profile.contract, repository)
    compiled, diagnostics = compile_translation_mappings(profile.contract, modules)
    assert not [item for item in diagnostics if item.severity == "error"]
    executable = next(iter(compiled.values()))
    assert executable.target.value.transform == "numeric_cast"
    assert executable.target.value.selector.unit == "unitless"
    records = [
        {
            "id": name,
            "ifc_class": "IfcSpace",
            "properties": {"Pset_SpaceOccupancyRequirements": {"OccupancyNumber": value}},
        }
        for name, value in [
            ("valid", 12),
            ("string", "12"),
            ("fraction", 1.5),
            ("boolean", True),
            ("overflow", 10**400),
            ("precision", 2**24 + 1),
        ]
    ]
    result = evaluate_ifc_mappings(compiled, records, "fixture")
    assert len(result.assertions) == 1 and result.assertions[0].object == 12
    assert {item.source_id for item in result.diagnostics} == {
        "string",
        "fraction",
        "boolean",
        "overflow",
        "precision",
    }


def test_module_direct_attribute_read_is_schema_checked_for_all_union_branches():
    profile, repository = module_case(
        'ifc:hasClassPredefinedType "IfcSpace, IfcZone" ; ifc:hasPsetProperty "ObjectType" ; ifc:hasDataType "String"',
        "a owl:DatatypeProperty ; schema:rangeIncludes xsd:string",
    )
    assert validate_translation_draft(profile, repository).authoring_valid


@pytest.mark.parametrize(
    "source, target, code",
    [
        (
            'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "Pset_DoorCommon.FireExit" ; '
            'ifc:hasDataType "Boolean"',
            "a owl:Class",
            "mapping_condition_required",
        ),
        (
            'ifc:hasClassPredefinedType "IfcZone" ; ifc:hasPsetProperty "FakeAttribute" ; '
            'ifc:hasDataType "String" ; ifc:hasValue "FireCompartment"',
            "a owl:Class",
            "mapping_attribute_unsupported",
        ),
        (
            'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "Pset_DoorCommon.FireExit" ; '
            'ifc:hasDataType "Boolean" ; ifc:hasValue "yes"',
            "a owl:Class",
            "mapping_literal_invalid",
        ),
        (
            'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "Pset_DoorCommon.FireExit" ; '
            'ifc:hasDataType "String" ; ifc:hasValue true',
            "a owl:Class",
            "mapping_literal_invalid",
        ),
        (
            'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "ObjectType" ; '
            'ifc:hasDataType "String" ; ifc:hasValue "<name>"',
            "a owl:Class",
            "mapping_interpretation_required",
        ),
        ('ifc:hasClassPredefinedType "IfcSpace, *"', "a owl:Class", "mapping_selector_unsupported"),
        ('ifc:hasClassPredefinedType "IfcSpace, IfcFacility"', "a owl:Class", "mapping_source_class_unsupported"),
        (
            'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "CPset_Test.Height" ; ifc:hasDataType "Length"',
            "a owl:DatatypeProperty ; schema:rangeIncludes xsd:float",
            "mapping_units_required",
        ),
        (
            'ifc:hasClassPredefinedType "IfcDoor" ; ifc:hasPsetProperty "CPset_Test.Flag" ; ifc:hasDataType "Boolean"',
            "a owl:DatatypeProperty ; schema:rangeIncludes xsd:string",
            "mapping_source_datatype_mismatch",
        ),
    ],
)
def test_extended_module_compilation_fails_closed_on_incomplete_or_ambiguous_semantics(source, target, code):
    profile, repository = module_case(source, target)
    report = validate_translation_draft(profile, repository)
    assert not report.authoring_valid
    assert any(item.code == code for item in report.diagnostics)


@pytest.mark.parametrize("mutation", ["edited", "missing_provenance", "replaced_release", "fire_rating"])
def test_review_does_not_bypass_seed_integrity_or_unresolved_interpretation(mutation):
    repository = Releases()
    profile = draft()
    choices = by_target(extract_module_mappings(profile, repository))
    target = "FireResistance" if mutation == "fire_rating" else "ExteriorWall"
    mapping = choices["https://firebim.org/ontologies/fbo#" + target]
    profile.contract.mappings = [mapping]
    mapping.enabled = True
    mapping.review_state = "accepted"
    if mutation == "edited":
        mapping.definition["source"]["values"] = ["False"]
    elif mutation == "missing_provenance":
        mapping.seed_provenance = None
    elif mutation == "replaced_release":
        repository.add("ifc", IFC + "\n# replaced under the same release\n")
    before = profile.model_dump(mode="json")
    report = validate_translation_draft(profile, repository)
    assert not report.authoring_valid
    if mutation == "fire_rating":
        assert any(d.code == "mapping_excluded_by_agreement" and d.mapping_id == mapping.id for d in report.diagnostics)
        assert any(d.code == "executable_mappings_required" and d.severity == "error" for d in report.diagnostics)
        modules, _ = load_translation_modules(profile.contract, repository)
        assert compile_translation_mappings(profile.contract, modules)[0] == {}
    else:
        assert any(d.severity == "error" and d.mapping_id == mapping.id for d in report.diagnostics)
    assert profile.model_dump(mode="json") == before


def test_snapshot_compilation_does_not_depend_on_selected_module_order():
    repository = Releases()
    repository.add(
        "other",
        """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://firebim.org/ontologies/fbo/other#> a owl:Ontology ;
    owl:versionIRI <https://firebim.org/ontologies/fbo/other/2026.09.04#> .
<https://firebim.org/ontologies/fbo#ExteriorWall> <http://www.w3.org/2000/01/rdf-schema#comment> "annotation" .
""",
    )
    profile = draft()
    contract_data = profile.contract.model_dump()
    contract_data["destination_modules"].insert(0, {"family": "fbo", "country": "other"})
    profile.contract = TranslationContract.model_validate(contract_data)
    mapping = by_target(extract_module_mappings(profile, repository))["https://firebim.org/ontologies/fbo#ExteriorWall"]
    profile.contract.mappings = [mapping]
    mapping.enabled, mapping.review_state = True, "accepted"
    assert validate_translation_draft(profile, repository).authoring_valid


def test_preserves_fire_rating_raw_rdf_and_leaves_wildcards_and_interpretation_unresolved():
    mapping = by_target(extract_module_mappings(draft(), Releases()))[
        "https://firebim.org/ontologies/fbo#FireResistance"
    ]
    assert mapping.enabled and mapping.review_state == "accepted"
    assert mapping.definition["source"]["values"] == ["<REI value string>", "REI60"]
    assert set(mapping.definition["review_issues"]) >= {
        "mapping_selector_unsupported",
        "mapping_interpretation_required",
    }
    assert "transform" not in mapping.definition
    statement = next(
        s
        for s in mapping.definition["declaration"]["statements"]
        if s["predicate"].endswith("#hasInterpretationResult")
    )
    assert statement["value"]["language"] == "en"


def test_repeat_extraction_preserves_existing_edits_rejections_and_disabled_entries():
    repository = Releases()
    profile = draft()
    first = extract_module_mappings(profile, repository)
    existing = first.mappings[0].model_copy(
        update={"enabled": False, "review_state": "rejected", "definition": {"user": "edit"}}
    )
    profile.contract.mappings = [existing]
    second = extract_module_mappings(profile, repository)
    assert len(second.mappings) == 2 and second.skipped_existing == 1
    assert existing.id not in {mapping.id for mapping in second.mappings}
    profile.contract.mappings.extend(second.mappings)
    assert extract_module_mappings(profile, repository).mappings == []
    assert profile.contract.mappings[0] == existing


def test_replaced_seed_content_does_not_silently_refresh_existing_mappings():
    repository = Releases()
    profile = draft()
    profile.contract.mappings = extract_module_mappings(profile, repository).mappings
    repository.add("ifc", IFC.replace('"True"', '"False"'))
    report = extract_module_mappings(profile, repository)
    assert not report.mappings and report.skipped_existing == 3
    assert any(d.code == "mapping_seed_changed" for d in report.diagnostics)


def test_provenance_prevents_duplicate_import_when_user_changes_a_mapping_id():
    repository = Releases()
    profile = draft()
    profile.contract.mappings = extract_module_mappings(profile, repository).mappings
    profile.contract.mappings[0].id = "my-reviewed-mapping"
    profile.contract.mappings[0].review_state = "rejected"
    report = extract_module_mappings(profile, repository)
    assert not report.mappings and report.skipped_existing == 3
    assert profile.contract.mappings[0].review_state == "rejected"


def test_changed_destination_definition_is_reported_without_overwriting_snapshot():
    repository = Releases()
    profile = draft()
    profile.contract.mappings = extract_module_mappings(profile, repository).mappings
    repository.add("core", CORE.replace("xsd:boolean", "xsd:string"))
    report = extract_module_mappings(profile, repository)
    assert not report.mappings and report.skipped_existing == 3
    assert any(d.code == "mapping_target_changed" for d in report.diagnostics)
    assert by_target(SimpleNamespace(mappings=profile.contract.mappings))[
        "https://firebim.org/ontologies/fbo#ExteriorWall"
    ].definition["target"]["ranges"] == ["http://www.w3.org/2001/XMLSchema#boolean"]


@pytest.mark.parametrize("failure", ["digest", "pin", "missing", "storage", "content", "dependencies"])
def test_unverified_or_incomplete_destination_closure_never_produces_suggestions(failure):
    repository = Releases()
    profile = draft()
    if failure == "digest":
        repository.items["ifc"].version.sha256 = "0" * 64
    elif failure == "pin":
        profile.contract.destination_modules[1].sha256 = "0" * 64
    elif failure == "missing":
        del repository.items["ifc"]
    elif failure == "storage":
        repository.items["ifc"] = GraphStoreError("offline")
    elif failure == "content":
        repository.add("ifc", "not Turtle")
    else:
        profile.contract.destination_modules = profile.contract.destination_modules[1:]
    report = extract_module_mappings(profile, repository)
    assert not report.mappings
    assert any(d.severity == "error" or d.code == "ontology_storage_unavailable" for d in report.diagnostics)


def test_inverse_link_requires_selected_mapping_module_and_never_fetches_linked_urls():
    profile = draft()
    profile.contract.destination_modules = profile.contract.destination_modules[:1]
    repository = Releases()
    report = extract_module_mappings(profile, repository)
    assert not report.mappings
    assert any(d.code == "mapping_module_missing" for d in report.diagnostics)
    assert repository.calls == [("fbo", "core", "2026.09.04")]


def test_inverse_only_and_multiple_target_mappings_and_ambiguous_ranges_are_retained():
    repository = Releases()
    repository.add("ifc", IFC.replace("ifc:mapsToTerm fbo:ExteriorWall", "ifc:mapsToTerm fbo:Building"))
    repository.add("core", CORE + "\nfbo:ExteriorWall schema:rangeIncludes xsd:string .\n")
    report = extract_module_mappings(draft(), repository)
    exterior = by_target(report)["https://firebim.org/ontologies/fbo#ExteriorWall"]
    assert exterior.definition["target"]["kind"] == "datatype_property"
    assert "mapping_target_range_unresolved" in exterior.definition["review_issues"]
    assert len(report.mappings) == 4


def test_order_is_deterministic_and_mapping_vocabulary_is_not_matched_by_local_name():
    repository = Releases()
    original = extract_module_mappings(draft(), repository)
    reordered = Graph().parse(data=IFC, format="turtle").serialize(format="turtle")
    repository.add("ifc", reordered)
    changed = extract_module_mappings(draft(), repository)
    assert [m.id for m in original.mappings] == [m.id for m in changed.mappings]
    repository.add("core", CORE.replace("fbo:hasIFCMapping", "<https://example.org/fake#hasIFCMapping>"))
    repository.add("ifc", IFC.replace("ifc:mapsToTerm", "<https://example.org/fake#mapsToTerm>"))
    assert not extract_module_mappings(draft(), repository).mappings


@pytest.mark.parametrize(
    ("declaration", "kind", "issue"),
    [
        ("fbo:Building a owl:DatatypeProperty .", None, "mapping_target_kind_unresolved"),
        ("fbo:ExteriorWall a owl:Class .", None, "mapping_target_kind_unresolved"),
        ("fbo:Building a owl:ObjectProperty .", None, "mapping_target_kind_unresolved"),
    ],
)
def test_ambiguous_destination_kinds_are_never_guessed(declaration, kind, issue):
    repository = Releases()
    repository.add("core", CORE + declaration)
    report = extract_module_mappings(draft(), repository)
    target = "ExteriorWall" if "ExteriorWall" in declaration else "Building"
    mapping = by_target(report)[f"https://firebim.org/ontologies/fbo#{target}"]
    assert mapping.definition["target"]["kind"] is kind
    assert issue in mapping.definition["review_issues"]


def test_object_property_kind_comes_from_destination_and_has_no_invented_relationship_binding():
    repository = Releases()
    repository.add("core", CORE.replace("fbo:Building a owl:Class", "fbo:Building a owl:ObjectProperty"))
    mapping = by_target(extract_module_mappings(draft(), repository))["https://firebim.org/ontologies/fbo#Building"]
    assert mapping.definition["target"]["kind"] == "object_property"
    assert "object_binding" not in mapping.definition
    assert mapping.enabled and mapping.review_state == "accepted"


def test_blank_or_literal_mapping_links_are_reported_not_followed():
    repository = Releases()
    repository.add(
        "ifc",
        IFC
        + """
ifc:Broken ifc:mapsToTerm "https://firebim.org/ontologies/fbo#Building" .
[] ifc:mapsToTerm fbo:Building .
""",
    )
    report = extract_module_mappings(draft(), repository)
    assert len(report.mappings) == 3
    assert sum(d.code == "mapping_link_invalid" for d in report.diagnostics) == 2
