from rdflib import RDF, RDFS, Graph, URIRef
from rdflib.namespace import OWL

from app.lexicon.dictionary_ontology import convert_dictionary_payload


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
