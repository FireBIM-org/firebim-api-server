from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD


@dataclass(frozen=True)
class DictionaryOntologyConversionResult:
    ontology_family: str
    country: str
    version: str
    source_dictionary_name: str | None
    source_dictionary_code: str | None
    source_dictionary_version: str | None
    class_count: int
    property_count: int
    datatype_property_count: int
    object_property_count: int
    ontology_uri: str
    version_iri: str
    ttl: str


def normalize_dictionary_json(raw_text: str) -> dict:
    normalized_text = re.sub(r",(\s*[\]}])", r"\1", raw_text)
    return json.loads(normalized_text)


def convert_dictionary_payload(
    payload: dict,
    *,
    ontology_family: str,
    country: str,
    version: str,
) -> DictionaryOntologyConversionResult:
    graph = Graph()
    base_uri = f"https://firebim.org/ontologies/{ontology_family}/{country}#"
    version_uri = f"https://firebim.org/ontologies/{ontology_family}/{country}/{version}#"
    ns = Namespace(base_uri)
    meta = Namespace("https://firebim.org/ontology-meta#")

    graph.bind(ontology_family, ns)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("skos", SKOS)
    graph.bind("meta", meta)

    ontology_subject = URIRef(base_uri)
    graph.add((ontology_subject, RDF.type, OWL.Ontology))
    graph.add((ontology_subject, OWL.versionIRI, URIRef(version_uri)))
    _add_literal(graph, ontology_subject, RDFS.label, payload.get("DictionaryName") or ontology_family.upper())
    _add_literal(
        graph,
        ontology_subject,
        RDFS.comment,
        f"{payload.get('DictionaryName', ontology_family.upper())} ontology projection generated from JSON.",
    )
    _add_literal(graph, ontology_subject, OWL.versionInfo, payload.get("DictionaryVersion"))
    _add_literal(graph, ontology_subject, meta.dictionaryCode, payload.get("DictionaryCode"))
    _add_literal(graph, ontology_subject, meta.sourceReleaseDate, payload.get("ReleaseDate"))
    _add_literal(graph, ontology_subject, meta.sourceStatus, payload.get("Status"))
    _add_literal(graph, ontology_subject, meta.sourceLicense, payload.get("License"))
    _add_literal(graph, ontology_subject, meta.moreInfoUrl, payload.get("MoreInfoUrl"))

    datatype_property_count = 0
    object_property_count = 0

    for item in payload.get("Properties", []):
        code = item.get("Code")
        if not code:
            continue
        subject = ns[_slug(code)]
        property_type = _property_rdf_type(item)
        graph.add((subject, RDF.type, property_type))
        if property_type == OWL.ObjectProperty:
            object_property_count += 1
        else:
            datatype_property_count += 1

        _add_literal(graph, subject, RDFS.label, item.get("Name") or code)
        _add_literal(graph, subject, SKOS.definition, item.get("Definition"))
        _add_literal(graph, subject, RDFS.comment, item.get("Description"))
        _add_literal(graph, subject, meta.sourceCode, code)
        _add_literal(graph, subject, meta.propertyCode, item.get("PropertyCode"))
        _add_literal(graph, subject, meta.propertySet, item.get("PropertySet"))
        _add_literal(graph, subject, meta.propertyValueKind, item.get("PropertyValueKind"))
        _add_literal(graph, subject, meta.activationDateUtc, item.get("ActivationDateUtc"))
        _add_literal(graph, subject, meta.versionDateUtc, item.get("VersionDateUtc"))
        datatype_uri = _datatype_uri(item.get("DataType"))
        if datatype_uri is not None:
            graph.add((subject, RDFS.range, datatype_uri))
        for raw_value in item.get("AllowedValues", []):
            graph.add((subject, meta.allowedValue, Literal(str(raw_value))))

    class_count = 0
    for item in payload.get("Classes", []):
        code = item.get("Code")
        if not code:
            continue
        class_count += 1
        subject = ns[_slug(code)]
        class_type = str(item.get("ClassType") or "Class")
        graph.add((subject, RDF.type, OWL.Class))
        if class_type == "GroupOfProperties":
            graph.add((subject, meta.groupOfProperties, Literal(True)))

        _add_literal(graph, subject, RDFS.label, item.get("Name") or code)
        _add_literal(graph, subject, SKOS.definition, item.get("Definition"))
        _add_literal(graph, subject, RDFS.comment, item.get("Description"))
        _add_literal(graph, subject, meta.sourceCode, code)
        _add_literal(graph, subject, meta.classType, class_type)
        _add_literal(graph, subject, meta.uid, item.get("Uid"))
        _add_literal(graph, subject, meta.parentClassCode, item.get("ParentClassCode"))
        _add_literal(graph, subject, meta.activationDateUtc, item.get("ActivationDateUtc"))
        _add_literal(graph, subject, meta.versionDateUtc, item.get("VersionDateUtc"))

        parent_class_code = item.get("ParentClassCode")
        if parent_class_code:
            graph.add((subject, RDFS.subClassOf, ns[_slug(parent_class_code)]))

        for relation in item.get("ClassRelations", []):
            relation_type = relation.get("RelationType")
            related_class_code = relation.get("RelatedClassCode")
            related_uri = relation.get("RelatedClassUri")
            if related_class_code:
                related_object = ns[_slug(related_class_code)]
            elif related_uri:
                related_object = URIRef(related_uri)
            else:
                continue

            if relation_type == "IsChildOf":
                graph.add((subject, RDFS.subClassOf, related_object))
            elif relation_type:
                relation_predicate = meta[_slug(relation_type)]
                graph.add((subject, relation_predicate, related_object))

        for class_property in item.get("ClassProperties", []):
            property_code = class_property.get("Code") or class_property.get("PropertyCode")
            if not property_code:
                continue
            property_subject = ns[_slug(property_code)]
            graph.add((subject, meta.hasProperty, property_subject))
            _add_literal(graph, property_subject, meta.propertyCode, class_property.get("PropertyCode"))
            _add_literal(graph, property_subject, meta.propertySet, class_property.get("PropertySet"))

    ttl = graph.serialize(format="turtle")
    if isinstance(ttl, bytes):
        ttl = ttl.decode("utf-8")

    return DictionaryOntologyConversionResult(
        ontology_family=ontology_family,
        country=country,
        version=version,
        source_dictionary_name=payload.get("DictionaryName"),
        source_dictionary_code=payload.get("DictionaryCode"),
        source_dictionary_version=payload.get("DictionaryVersion"),
        class_count=class_count,
        property_count=datatype_property_count + object_property_count,
        datatype_property_count=datatype_property_count,
        object_property_count=object_property_count,
        ontology_uri=base_uri,
        version_iri=version_uri,
        ttl=ttl,
    )


def convert_dictionary_json_text(
    raw_text: str,
    *,
    ontology_family: str,
    country: str,
    version: str,
) -> DictionaryOntologyConversionResult:
    payload = normalize_dictionary_json(raw_text)
    return convert_dictionary_payload(
        payload,
        ontology_family=ontology_family,
        country=country,
        version=version,
    )


def _slug(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value).strip("_") or "term"


def _datatype_uri(data_type: str | None) -> URIRef | None:
    mapping = {
        "boolean": XSD.boolean,
        "real": XSD.decimal,
        "number": XSD.decimal,
        "integer": XSD.integer,
        "int": XSD.integer,
        "string": XSD.string,
        "date": XSD.date,
        "datetime": XSD.dateTime,
    }
    if not data_type:
        return None
    return mapping.get(data_type.strip().lower())


def _property_rdf_type(item: dict) -> URIRef:
    property_value_kind = str(item.get("PropertyValueKind") or "").strip().lower()
    description = str(item.get("Description") or "").lower()
    if property_value_kind in {"complex", "reference"}:
        return OWL.ObjectProperty
    if "reference to ifc" in description:
        return OWL.ObjectProperty
    return OWL.DatatypeProperty


def _add_literal(graph: Graph, subject: URIRef, predicate: URIRef, value: str | None) -> None:
    if value:
        graph.add((subject, predicate, Literal(value)))
