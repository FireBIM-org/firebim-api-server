from rdflib import RDF, RDFS, Graph, URIRef
from rdflib.namespace import OWL

from app.ontologies.dictionary_ontology import convert_dictionary_payload


def test_rir_dictionary_items_are_converted_as_classes_and_properties():
    conversion = convert_dictionary_payload(
        {
            "DictionaryName": "RIR",
            "DictionaryCode": "rir",
            "DictionaryVersion": "1.0",
            "Classes": [
                {
                    "Code": "FireCompartment",
                    "Name": "Fire Compartment",
                    "ClassType": "Class",
                },
                {
                    "Code": "CompartmentProperties",
                    "Name": "Compartment Properties",
                    "ClassType": "GroupOfProperties",
                },
            ],
            "Properties": [
                {
                    "Code": "AreaFireCompartment",
                    "Name": "Area",
                    "DataType": "Real",
                }
            ],
        },
        ontology_family="rir",
        country="global",
        version="2026.03.25",
    )

    graph = Graph()
    graph.parse(data=conversion.ttl, format="turtle")

    compartment = URIRef("https://firebim.org/ontologies/rir/global#FireCompartment")
    property_group = URIRef("https://firebim.org/ontologies/rir/global#CompartmentProperties")
    area = URIRef("https://firebim.org/ontologies/rir/global#AreaFireCompartment")

    assert (compartment, RDF.type, OWL.Class) in graph
    assert (property_group, RDF.type, OWL.Class) in graph
    assert (area, RDF.type, OWL.DatatypeProperty) in graph
    assert conversion.class_count == 2
    assert conversion.property_count == 1


def test_ifc_parent_class_code_becomes_subclass_relationship():
    conversion = convert_dictionary_payload(
        {
            "DictionaryName": "IFC 4.3",
            "DictionaryCode": "ifc",
            "DictionaryVersion": "4.3",
            "Classes": [
                {
                    "Code": "IfcBuiltElement",
                    "Name": "Built Element",
                    "ClassType": "Class",
                },
                {
                    "Code": "IfcWall",
                    "Name": "Wall",
                    "ClassType": "Class",
                    "ParentClassCode": "IfcBuiltElement",
                },
            ],
            "Properties": [
                {
                    "Code": "PositionHistory",
                    "Name": "Position History",
                    "PropertyValueKind": "Complex",
                    "Description": "Reference to IFC object graph",
                }
            ],
        },
        ontology_family="ifc",
        country="global",
        version="2026.04.23",
    )

    graph = Graph()
    graph.parse(data=conversion.ttl, format="turtle")

    wall = URIRef("https://firebim.org/ontologies/ifc/global#IfcWall")
    built_element = URIRef("https://firebim.org/ontologies/ifc/global#IfcBuiltElement")
    position_history = URIRef("https://firebim.org/ontologies/ifc/global#PositionHistory")

    assert (wall, RDF.type, OWL.Class) in graph
    assert (wall, RDFS.subClassOf, built_element) in graph
    assert (position_history, RDF.type, OWL.ObjectProperty) in graph
    assert conversion.object_property_count == 1


def test_dictionary_conversion_resolves_class_properties_and_reports_metrics():
    conversion = convert_dictionary_payload(
        {
            "DictionaryName": "Candidate",
            "DictionaryCode": "cand",
            "DictionaryVersion": "1.0",
            "Classes": [
                {
                    "Code": "WallClass",
                    "Name": "Wall Class",
                    "ClassType": "Class",
                    "Definition": "Wall class definition",
                    "Description": "Wall class description",
                    "RelatedIfcEntityNamesList": ["IfcWall"],
                    "CountriesOfUse": ["DK"],
                    "Uid": "class-wall",
                    "ClassProperties": [
                        {
                            "PropertyCode": "Combustible",
                            "PropertySet": "Pset_WallCommon",
                            "OwnedUri": "prop-combustible",
                        }
                    ],
                }
            ],
            "Properties": [
                {
                    "Code": "Combustible",
                    "PropertyCode": "Combustible",
                    "PropertyUri": "https://example.org/props/combustible",
                    "Definition": "Combustible definition",
                    "Description": "Combustible property",
                    "Unit": "boolean",
                    "DataType": "Boolean",
                    "PropertyType": "SingleValue",
                    "PropertyValueKind": "Single",
                    "AllowedValues": [True, False],
                    "Example": "True",
                    "Uid": "prop-combustible",
                }
            ],
        },
        ontology_family="cand",
        country="global",
        version="2026.04.24",
    )

    graph = Graph()
    graph.parse(data=conversion.ttl, format="turtle")

    wall = URIRef("https://firebim.org/ontologies/cand/global#WallClass")
    combustible = URIRef("https://firebim.org/ontologies/cand/global#Combustible")
    has_property = URIRef("https://firebim.org/ontology-meta#hasProperty")
    belongs_to_class = URIRef("https://firebim.org/ontology-meta#belongsToClass")

    assert (wall, has_property, combustible) in graph
    assert (combustible, belongs_to_class, wall) in graph
    assert conversion.class_property_link_count == 1
    assert conversion.validation_report.valid is True
    assert conversion.validation_report.resolved_links_by_owned_uri == 1
    assert conversion.validation_report.ttl_has_class_property_links is True


def test_dictionary_conversion_reports_missing_fields_and_unresolved_links():
    conversion = convert_dictionary_payload(
        {
            "DictionaryName": "Broken Candidate",
            "DictionaryCode": "broken",
            "DictionaryVersion": "1.0",
            "Classes": [
                {
                    "Code": "BrokenWall",
                    "Name": "Broken Wall",
                    "ClassType": "Class",
                    "Definition": "Missing some fields",
                    "Description": "Broken description",
                    "CountriesOfUse": [],
                    "Uid": "broken-wall",
                    "ClassProperties": [
                        {
                            "PropertyCode": "UnknownProperty",
                        }
                    ],
                }
            ],
            "Properties": [
                {
                    "Code": "Combustible",
                    "PropertyCode": "Combustible",
                    "Description": "Combustible property",
                    "Unit": "boolean",
                    "DataType": "Boolean",
                    "PropertyType": "SingleValue",
                    "PropertyValueKind": "Single",
                    "AllowedValues": [],
                    "Example": "True",
                }
            ],
        },
        ontology_family="broken",
        country="global",
        version="2026.04.24",
    )

    report = conversion.validation_report
    assert report.valid is False
    assert report.completeness_ok is False
    assert report.unresolved_link_count == 1
    assert report.classes_missing_required_fields[0].item_code == "BrokenWall"
    assert "RelatedIfcEntityNamesList" in report.classes_missing_required_fields[0].missing_fields
    assert report.properties_missing_required_fields[0].item_code == "Combustible"
    assert "PropertyUri" in report.properties_missing_required_fields[0].missing_fields
