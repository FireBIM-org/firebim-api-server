import hashlib
import json
from pathlib import Path

import pytest
from rdflib import OWL, RDF, Graph, Literal, Namespace, URIRef
from rdflib import XSD as RDF_XSD

from app.ingestion.neo_ifcowl_adapter import NeoAdapterError, adapt_ifcowl
from app.lexicon.ifc_mapping_evaluator import evaluate_ifc_mappings
from app.schemas.ifc_mapping import ExecutableMapping

FIXTURES = Path(__file__).parent / "fixtures" / "neo"
EX = "https://example.org/destination/"
SOURCE = "https://example.org/neo-fixture/"
XSD = "http://www.w3.org/2001/XMLSchema#"
IFC = Namespace("https://standards.buildingsmart.org/IFC/DEV/IFC4/ADD2/OWL#")
EXPRESS = Namespace("https://w3id.org/express#")


def test_captured_source_and_ifc_bytes_match_the_recorded_converter_probe():
    provenance = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    assert hashlib.sha256((FIXTURES / provenance["source"]).read_bytes()).hexdigest() == provenance["source_sha256"]
    assert (
        hashlib.sha256((FIXTURES / provenance["captured_artifact"]).read_bytes()).hexdigest()
        == (provenance["captured_sha256"])
    )
    assert len(source_graph()) == provenance["triples"] == 381


def mapping(target=None, *, ifc_class="IfcWall", match="exact", subject="entity", condition=None):
    return ExecutableMapping.model_validate(
        {
            "language": "ifc4-mapping/1",
            "source": {"schema": "IFC4", "ifc_class": ifc_class, "match": match},
            "subject": subject,
            "target": target or {"kind": "class", "iri": EX + "Wall"},
            "condition": condition,
            "policies": {
                "identity": "model_scoped",
                "property_precedence": "occurrence_over_type",
                "missing": "skip",
                "invalid": "error",
                "conflicts": "error",
            },
        }
    )


def source_graph():
    return Graph().parse(FIXTURES / "semantic_ifcowl.ttl", format="turtle")


def read_target(name="IsExternal", *, datatype="boolean", pset="Pset_WallCommon", kind="property", unit=None):
    selector = {"kind": kind, "name": name, "datatype": XSD + datatype}
    if kind != "attribute":
        selector["pset"] = pset
    if unit is not None:
        selector["unit"] = unit
    return {
        "kind": "datatype_property",
        "iri": EX + name,
        "datatype": XSD + datatype,
        "value": {"kind": "read", "selector": selector},
        "cardinality": "single",
    }


def test_exact_types_and_guid_identities_are_preserved_without_reasoning():
    source = adapt_ifcowl(source_graph())
    records = {record["attributes"]["Name"]: record for record in source.records if "Name" in record["attributes"]}
    assert records["Inherited"]["id"] == "0000000000000000000010"
    assert records["Override"]["ifc_class"] == "IfcWallStandardCase"
    assert records["Override"]["predefined_type"] == "STANDARD"
    identity = source.identities["0000000000000000000011"]
    assert (identity.iri, identity.step_id, identity.guid) == (
        SOURCE + "IfcWallStandardCase_11",
        11,
        "0000000000000000000011",
    )
    exact = evaluate_ifc_mappings({"wall": mapping()}, source.records, "m")
    assert len(exact.assertions) == 4
    subtypes = evaluate_ifc_mappings({"wall": mapping(match="subtypes")}, source.records, "m")
    assert len(subtypes.assertions) == 5
    other = evaluate_ifc_mappings({"wall": mapping()}, source.records, "other")
    assert {item.subject for item in exact.assertions}.isdisjoint(item.subject for item in other.assertions)


def test_owned_properties_preserve_per_key_inheritance_null_masking_and_conflicts():
    source = adapt_ifcowl(source_graph())
    result = evaluate_ifc_mappings(
        {
            "external": mapping(read_target(), match="subtypes"),
            "reference": mapping(read_target("Reference", datatype="string"), match="subtypes"),
        },
        source.records,
        "m",
    )
    assert {(item.subject, item.predicate, item.object) for item in result.assertions} == {
        ("urn:firebim:model:m:entity:0000000000000000000010", EX + "IsExternal", True),
        ("urn:firebim:model:m:entity:0000000000000000000011", EX + "IsExternal", False),
        ("urn:firebim:model:m:entity:0000000000000000000010", EX + "Reference", "from type"),
        ("urn:firebim:model:m:entity:0000000000000000000011", EX + "Reference", "from type"),
    }
    assert [item.code for item in result.diagnostics] == ["cardinality_conflict"]
    external = [item for item in result.assertions if item.predicate == EX + "IsExternal"]
    assert {item.evidence[0].origin for item in external} == {"type", "occurrence"}
    records = {record["id"]: record for record in source.records}
    assert records["0000000000000000000012"]["properties"]["Pset_WallCommon"] == {
        "IsExternal": None,
        "Reference": None,
    }


def test_measurements_keep_model_and_value_units_and_do_not_replace_primary_with_fallback():
    source = adapt_ifcowl(source_graph())
    assert source.model_units == {
        "length": {"symbol": "mm", "dimension": "length", "scale_to_si": 0.001},
        "area": {"symbol": "m2", "dimension": "area", "scale_to_si": 1},
    }
    area = read_target("CompartmentArea", datatype="double", pset="CPset_FireBIM", unit="ifc_area")
    area["value"]["fallback"] = [
        {
            "kind": "quantity",
            "pset": "Qto_SpatialZoneOtherBaseQuantities",
            "name": "NetFloorArea",
            "datatype": XSD + "double",
            "unit": "ifc_area",
        }
    ]
    result = evaluate_ifc_mappings(
        {
            "length": mapping(
                read_target(
                    "Length", datatype="double", pset="Qto_WallBaseQuantities", kind="quantity", unit="ifc_length"
                ),
                match="subtypes",
            ),
            "width": mapping(
                read_target("OverallWidth", datatype="double", kind="attribute", unit="ifc_length"), ifc_class="IfcDoor"
            ),
            "area": mapping(area, ifc_class="IfcSpace"),
        },
        source.records,
        "m",
        model_units=source.model_units,
    )
    assert {(item.subject, item.object, item.unit.symbol, item.unit.scale_to_si) for item in result.assertions} == {
        ("urn:firebim:model:m:entity:0000000000000000000010", 1200.0, "mm", 0.001),
        ("urn:firebim:model:m:entity:0000000000000000000011", 1.5, "m", 1),
        ("urn:firebim:model:m:entity:0000000000000000000012", 1200.0, "mm", 0.001),
        ("urn:firebim:model:m:entity:0000000000000000000015", 900.0, "mm", 0.001),
        ("urn:firebim:model:m:entity:0000000000000000000007", 25.0, "m2", 1),
        ("urn:firebim:model:m:entity:0000000000000000000008", 25.0, "m2", 1),
    }
    assert [(item.code, item.severity) for item in result.diagnostics] == [("mapping_source_disagreement", "warning")]


def test_explicit_spatial_endpoints_and_group_memberships_reach_the_existing_evaluator():
    graph = source_graph()
    source = adapt_ifcowl(graph)
    mappings = {
        "aggregation": mapping(
            {"kind": "object_property", "iri": EX + "hasPart", "object": "related", "cardinality": "many"},
            ifc_class="IfcRelAggregates",
            subject="relating",
        ),
        "containment": mapping(
            {"kind": "object_property", "iri": EX + "contains", "object": "related", "cardinality": "many"},
            ifc_class="IfcRelContainedInSpatialStructure",
            subject="relating",
        ),
        "group": mapping(
            {
                "kind": "class",
                "iri": EX + "Compartment",
                "grouping": {
                    "kind": "property",
                    "pset": "CPset_FireBIM",
                    "name": "Compartment",
                    "datatype": XSD + "string",
                },
            },
            ifc_class="IfcSpace",
        ),
    }
    result = evaluate_ifc_mappings(mappings, source.records, "m")
    assert not result.diagnostics
    edges = {
        (item.subject.rsplit(":", 1)[-1], item.predicate, item.object.rsplit(":", 1)[-1])
        for item in result.assertions
        if item.predicate != str(RDF.type)
    }
    assert edges == {
        ("0000000000000000000004", EX + "hasPart", "0000000000000000000005"),
        ("0000000000000000000005", EX + "hasPart", "0000000000000000000006"),
        ("0000000000000000000006", EX + "hasPart", "0000000000000000000007"),
        ("0000000000000000000006", EX + "hasPart", "0000000000000000000008"),
        ("0000000000000000000006", EX + "contains", "0000000000000000000010"),
        ("0000000000000000000006", EX + "contains", "0000000000000000000011"),
        ("0000000000000000000006", EX + "contains", "0000000000000000000012"),
        ("0000000000000000000006", EX + "contains", "0000000000000000000013"),
        ("0000000000000000000006", EX + "contains", "0000000000000000000014"),
        ("0000000000000000000006", EX + "contains", "0000000000000000000015"),
    }
    groups = [item for item in result.assertions if item.object == EX + "Compartment"]
    assert len(groups) == 1
    assert len(result.memberships) == 2
    assert {item.group for item in result.memberships} == {groups[0].subject}
    assert {item.member for item in result.memberships} == {
        "urn:firebim:model:m:entity:0000000000000000000007",
        "urn:firebim:model:m:entity:0000000000000000000008",
    }
    reversed_graph = Graph()
    for triple in reversed(list(graph)):
        reversed_graph.add(triple)
    repeated = adapt_ifcowl(reversed_graph)
    assert result == evaluate_ifc_mappings(mappings, repeated.records, "m")


def test_null_conflicting_with_a_value_is_not_resolved_as_a_scalar():
    graph = source_graph()
    graph.remove((URIRef(SOURCE + "IfcPropertySingleValue_52"), IFC.nominalValue_IfcPropertySingleValue, None))
    with pytest.raises(NeoAdapterError) as caught:
        adapt_ifcowl(graph)
    assert caught.value.code == "conflicting_source_null"
    assert caught.value.source_iri == SOURCE + "IfcPropertySingleValue_52"


@pytest.mark.parametrize(
    ("entity", "predicate", "replacement", "code"),
    [
        ("IfcWallStandardCase_11", RDF.type, IFC.IfcWall, "ambiguous_source_class"),
        ("IfcSIUnit_45", IFC.unitType_IfcNamedUnit, IFC.AREAUNIT, "invalid_source_unit"),
        ("IfcSIUnit_1", IFC.prefix_IfcSIUnit, IFC.MADE_UP, "unsupported_source_unit"),
        ("IfcBoolean_105", EXPRESS.hasBoolean, Literal("NaN", datatype=RDF_XSD.double), "invalid_source_literal"),
        ("IfcPropertySingleValue_31", RDF.type, IFC.IfcPropertyEnumeratedValue, "ambiguous_source_class"),
        (
            "IfcRelContainedInSpatialStructure_73",
            IFC.relatingStructure_IfcRelContainedInSpatialStructure,
            URIRef(SOURCE + "IfcBuildingStorey_999"),
            "dangling_relationship_endpoint",
        ),
    ],
)
def test_malformed_or_unsupported_source_is_rejected_with_an_actionable_code(entity, predicate, replacement, code):
    graph = source_graph()
    subject = URIRef(SOURCE + entity)
    if predicate != RDF.type:
        graph.remove((subject, predicate, None))
    graph.add((subject, predicate, replacement))
    with pytest.raises(NeoAdapterError) as caught:
        adapt_ifcowl(graph)
    assert caught.value.code == code
    assert caught.value.source_iri == str(subject)


def test_same_local_name_in_an_incompatible_ifc_namespace_is_not_accepted():
    graph = source_graph()
    graph.remove((None, OWL.imports, None))
    graph.add((URIRef(SOURCE), OWL.imports, URIRef("https://example.org/not-the-pinned-schema/IFC4#")))
    with pytest.raises(NeoAdapterError) as caught:
        adapt_ifcowl(graph)
    assert caught.value.code == "unsupported_source_schema"


def test_duplicate_guid_is_not_allowed_to_merge_elements():
    graph = source_graph()
    node = graph.value(URIRef(SOURCE + "IfcWall_13"), IFC.globalId_IfcRoot)
    graph.set((node, EXPRESS.hasString, Literal("0000000000000000000010")))
    with pytest.raises(NeoAdapterError) as caught:
        adapt_ifcowl(graph)
    assert caught.value.code == "duplicate_source_identity"


def test_logical_values_are_not_coerced_into_booleans():
    graph = source_graph()
    node = URIRef(SOURCE + "IfcBoolean_105")
    graph.remove((node, EXPRESS.hasBoolean, None))
    graph.add((node, EXPRESS.hasLogical, EXPRESS.TRUE))
    with pytest.raises(NeoAdapterError) as caught:
        adapt_ifcowl(graph)
    assert caught.value.code == "unsupported_source_value"


def test_property_provenance_retains_both_candidates_and_their_owners():
    source = adapt_ifcowl(source_graph())
    candidates = [
        item
        for item in source.evidence
        if item.source_id == "0000000000000000000011"
        and item.container.endswith("properties")
        and item.name == "IsExternal"
    ]
    assert {
        (item.container, item.pset_iri, item.value_iri, item.relationship_iri, item.type_iri) for item in candidates
    } == {
        (
            "properties",
            SOURCE + "IfcPropertySet_36",
            SOURCE + "IfcPropertySingleValue_37",
            SOURCE + "IfcRelDefinesByProperties_40",
            None,
        ),
        (
            "type_properties",
            SOURCE + "IfcPropertySet_30",
            SOURCE + "IfcPropertySingleValue_31",
            SOURCE + "IfcRelDefinesByType_21",
            SOURCE + "IfcWallType_20",
        ),
    }


def test_mapping_scoped_projection_ignores_unrequested_property_forms_but_never_a_requested_one():
    graph = source_graph()
    old = URIRef(SOURCE + "IfcPropertySingleValue_55")
    prop = URIRef(SOURCE + "IfcPropertyEnumeratedValue_55")
    for subject, predicate, value in list(graph):
        if old in {subject, value}:
            graph.remove((subject, predicate, value))
            graph.add((prop if subject == old else subject, predicate, prop if value == old else value))
    graph.set((prop, RDF.type, IFC.IfcPropertyEnumeratedValue))
    selected = {"external": mapping(read_target(), match="subtypes")}
    source = adapt_ifcowl(graph, mappings=selected)
    result = evaluate_ifc_mappings(selected, source.records, "m")
    assert len(result.assertions) == 2
    assert [item.code for item in result.diagnostics] == ["cardinality_conflict"]
    assert not any(item.pset == "UnrelatedSet" for item in source.evidence)
    selected["unsupported"] = mapping(read_target(pset="UnrelatedSet"))
    with pytest.raises(NeoAdapterError) as caught:
        adapt_ifcowl(graph, mappings=selected)
    assert caught.value.code == "unsupported_property_form"
    assert caught.value.source_iri == str(prop)


def test_scoped_reads_include_nested_conditions_grouping_fallbacks_and_union_selectors():
    area = read_target("MissingArea", datatype="double", pset="CPset_FireBIM", unit="ifc_area")
    area["value"]["fallback"] = [
        {
            "kind": "quantity",
            "pset": "Qto_SpatialZoneOtherBaseQuantities",
            "name": "NetFloorArea",
            "datatype": XSD + "double",
            "unit": "ifc_area",
        }
    ]
    selected = {
        "conditional": mapping(
            ifc_class="IfcWallStandardCase",
            condition={
                "kind": "all_of",
                "conditions": [
                    {"kind": "equals", "selector": read_target()["value"]["selector"], "values": [False]},
                    {
                        "kind": "any_of",
                        "conditions": [
                            {
                                "kind": "present",
                                "selector": {
                                    "kind": "property",
                                    "pset": "Pset_WallCommon",
                                    "name": "Reference",
                                    "datatype": XSD + "string",
                                },
                            }
                        ],
                    },
                ],
            },
        ),
        "area": mapping(area, ifc_class="IfcSpace"),
        "group": mapping(
            {
                "kind": "class",
                "iri": EX + "Compartment",
                "grouping": {
                    "kind": "property",
                    "pset": "CPset_FireBIM",
                    "name": "Compartment",
                    "datatype": XSD + "string",
                },
            },
            ifc_class="IfcSpace",
        ),
    }
    union = mapping().model_dump(mode="json")
    union["source"] = {
        "schema": "IFC4",
        "any_of": [
            {"schema": "IFC4", "ifc_class": "IfcDoor", "match": "exact"},
            {"schema": "IFC4", "ifc_class": "IfcSpace", "match": "exact"},
        ],
    }
    selected["union"] = ExecutableMapping.model_validate(union)
    source = adapt_ifcowl(source_graph(), mappings=selected)
    result = evaluate_ifc_mappings(selected, source.records, "m", model_units=source.model_units)
    assert len(result.assertions) == 6
    assert len(result.memberships) == 2
    assert not result.diagnostics
    assert any(item.object == 24.0 for item in result.assertions)
    conditional = next(item for item in result.assertions if item.subject.endswith("0011"))
    assert {(item.kind, item.origin) for item in conditional.evidence} == {
        ("entity", None),
        ("property", "occurrence"),
        ("property", "type"),
    }
