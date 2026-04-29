from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

META = Namespace("https://firebim.org/ontology-meta#")

REQUIRED_CLASS_FIELDS = (
    "Code",
    "Name",
    "ClassType",
    "Definition",
    "Description",
    "RelatedIfcEntityNamesList",
    "CountriesOfUse",
    "Uid",
)
REQUIRED_PROPERTY_FIELDS = (
    "Code",
    "PropertyCode",
    "PropertyUri",
    "Description",
    "Unit",
    "DataType",
    "PropertyType",
    "PropertyValueKind",
    "AllowedValues",
    "Example",
)


@dataclass(frozen=True)
class DictionaryFieldIssue:
    item_code: str
    missing_fields: tuple[str, ...]


@dataclass(frozen=True)
class DictionaryLinkIssue:
    class_code: str
    class_property_code: str | None
    owned_uri: str | None
    reason: str
    candidate_property_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DictionaryOntologyValidationReport:
    valid: bool
    shape_ok: bool
    completeness_ok: bool
    ttl_form_ok: bool
    source_dictionary_name: str | None
    source_dictionary_code: str | None
    source_dictionary_version: str | None
    class_count: int
    subclass_count: int
    property_count: int
    datatype_property_count: int
    object_property_count: int
    class_property_link_count: int
    resolved_links_by_owned_uri: int
    resolved_links_by_code: int
    resolved_links_by_name: int
    unresolved_link_count: int
    ambiguous_link_count: int
    classes_missing_required_fields: tuple[DictionaryFieldIssue, ...]
    properties_missing_required_fields: tuple[DictionaryFieldIssue, ...]
    unresolved_class_property_links: tuple[DictionaryLinkIssue, ...]
    ambiguous_class_property_links: tuple[DictionaryLinkIssue, ...]
    ttl_has_ontology: bool
    ttl_has_classes: bool
    ttl_has_properties: bool
    ttl_has_class_property_links: bool
    issues: tuple[str, ...]


@dataclass(frozen=True)
class DictionaryOntologyConversionResult:
    ontology_family: str
    country: str
    version: str
    source_dictionary_name: str | None
    source_dictionary_code: str | None
    source_dictionary_version: str | None
    class_count: int
    subclass_count: int
    property_count: int
    datatype_property_count: int
    object_property_count: int
    class_property_link_count: int
    ontology_uri: str
    version_iri: str
    ttl: str
    validation_report: DictionaryOntologyValidationReport


@dataclass(frozen=True)
class _ResolvedPropertyLink:
    property_item: dict
    subject: URIRef
    resolution: str


@dataclass(frozen=True)
class _DictionaryConversionContext:
    graph: Graph
    class_count: int
    subclass_count: int
    property_count: int
    datatype_property_count: int
    object_property_count: int
    class_property_link_count: int
    resolved_links_by_owned_uri: int
    resolved_links_by_code: int
    resolved_links_by_name: int
    unresolved_link_issues: tuple[DictionaryLinkIssue, ...]
    ambiguous_link_issues: tuple[DictionaryLinkIssue, ...]


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
    payload = payload or {}
    context = _build_dictionary_graph(
        payload,
        ontology_family=ontology_family,
        country=country,
        version=version,
    )
    ttl = context.graph.serialize(format="turtle")
    if isinstance(ttl, bytes):
        ttl = ttl.decode("utf-8")

    validation_report = _build_validation_report(payload=payload, context=context)
    return DictionaryOntologyConversionResult(
        ontology_family=ontology_family,
        country=country,
        version=version,
        source_dictionary_name=payload.get("DictionaryName"),
        source_dictionary_code=payload.get("DictionaryCode"),
        source_dictionary_version=payload.get("DictionaryVersion"),
        class_count=context.class_count,
        subclass_count=context.subclass_count,
        property_count=context.property_count,
        datatype_property_count=context.datatype_property_count,
        object_property_count=context.object_property_count,
        class_property_link_count=context.class_property_link_count,
        ontology_uri=f"https://firebim.org/ontologies/{ontology_family}/{country}#",
        version_iri=f"https://firebim.org/ontologies/{ontology_family}/{country}/{version}#",
        ttl=ttl,
        validation_report=validation_report,
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


def _build_dictionary_graph(
    payload: dict,
    *,
    ontology_family: str,
    country: str,
    version: str,
) -> _DictionaryConversionContext:
    graph = Graph()
    base_uri = f"https://firebim.org/ontologies/{ontology_family}/{country}#"
    version_uri = f"https://firebim.org/ontologies/{ontology_family}/{country}/{version}#"
    versioned_ns = Namespace(version_uri)

    graph.bind(ontology_family, versioned_ns)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("skos", SKOS)
    graph.bind("meta", META)

    graph.add((META.hasProperty, OWL.inverseOf, META.belongsToClass))

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
    _add_literal(graph, ontology_subject, META.dictionaryCode, payload.get("DictionaryCode"))
    _add_literal(graph, ontology_subject, META.sourceReleaseDate, payload.get("ReleaseDate"))
    _add_literal(graph, ontology_subject, META.sourceStatus, payload.get("Status"))
    _add_literal(graph, ontology_subject, META.sourceLicense, payload.get("License"))
    _add_literal(graph, ontology_subject, META.moreInfoUrl, payload.get("MoreInfoUrl"))

    property_subjects: dict[int, URIRef] = {}
    properties = payload.get("Properties", [])
    classes = payload.get("Classes", [])

    datatype_property_count = 0
    object_property_count = 0
    for index, item in enumerate(properties):
        code = _property_term_code(item, fallback=f"property_{index + 1}")
        subject = versioned_ns[_slug(code)]
        property_subjects[index] = subject
        property_type = _property_rdf_type(item)
        graph.add((subject, RDF.type, property_type))
        if property_type == OWL.ObjectProperty:
            object_property_count += 1
        else:
            datatype_property_count += 1

        _add_literal(graph, subject, RDFS.label, item.get("Name") or code)
        _add_literal(graph, subject, SKOS.definition, item.get("Definition"))
        _add_literal(graph, subject, RDFS.comment, item.get("Description"))
        _add_literal(graph, subject, META.sourceCode, item.get("Code"))
        _add_literal(graph, subject, META.propertyCode, item.get("PropertyCode"))
        _add_literal(graph, subject, META.propertyUri, item.get("PropertyUri"))
        _add_literal(graph, subject, META.uid, item.get("Uid"))
        _add_literal(graph, subject, META.propertySet, item.get("PropertySet"))
        _add_literal(graph, subject, META.unit, item.get("Unit"))
        _add_literal(graph, subject, META.dataType, item.get("DataType"))
        _add_literal(graph, subject, META.propertyType, item.get("PropertyType"))
        _add_literal(graph, subject, META.propertyValueKind, item.get("PropertyValueKind"))
        _add_literal(graph, subject, META.example, item.get("Example"))
        _add_literal(graph, subject, META.activationDateUtc, item.get("ActivationDateUtc"))
        _add_literal(graph, subject, META.versionDateUtc, item.get("VersionDateUtc"))
        datatype_uri = _datatype_uri(item.get("DataType"))
        if datatype_uri is not None:
            graph.add((subject, RDFS.range, datatype_uri))
        for raw_value in item.get("AllowedValues", []):
            graph.add((subject, META.allowedValue, Literal(str(raw_value))))
        for connected_code in item.get("ConnectedPropertyCodes", []):
            graph.add((subject, META.connectedPropertyCode, Literal(str(connected_code))))

    property_lookup = _build_property_lookup(properties, property_subjects)

    subclass_pairs: set[tuple[URIRef, URIRef]] = set()
    class_property_pairs: set[tuple[URIRef, URIRef]] = set()
    unresolved_link_issues: list[DictionaryLinkIssue] = []
    ambiguous_link_issues: list[DictionaryLinkIssue] = []
    resolved_links_by_owned_uri = 0
    resolved_links_by_code = 0
    resolved_links_by_name = 0

    for index, item in enumerate(classes):
        code = _class_term_code(item, fallback=f"class_{index + 1}")
        subject = versioned_ns[_slug(code)]
        class_type = str(item.get("ClassType") or "Class")
        graph.add((subject, RDF.type, OWL.Class))
        if class_type == "GroupOfProperties":
            graph.add((subject, META.groupOfProperties, Literal(True)))

        _add_literal(graph, subject, RDFS.label, item.get("Name") or code)
        _add_literal(graph, subject, SKOS.definition, item.get("Definition"))
        _add_literal(graph, subject, RDFS.comment, item.get("Description"))
        _add_literal(graph, subject, META.sourceCode, item.get("Code"))
        _add_literal(graph, subject, META.classType, class_type)
        _add_literal(graph, subject, META.uid, item.get("Uid"))
        _add_literal(graph, subject, META.parentClassCode, item.get("ParentClassCode"))
        _add_literal(graph, subject, META.activationDateUtc, item.get("ActivationDateUtc"))
        _add_literal(graph, subject, META.versionDateUtc, item.get("VersionDateUtc"))
        for related_ifc_name in item.get("RelatedIfcEntityNamesList", []):
            graph.add((subject, META.relatedIfcEntityName, Literal(str(related_ifc_name))))
        for country_of_use in item.get("CountriesOfUse", []):
            graph.add((subject, META.countryOfUse, Literal(str(country_of_use))))

        parent_class_code = item.get("ParentClassCode")
        if parent_class_code:
            parent_subject = versioned_ns[_slug(str(parent_class_code))]
            graph.add((subject, RDFS.subClassOf, parent_subject))
            subclass_pairs.add((subject, parent_subject))

        for relation in item.get("ClassRelations", []):
            relation_type = relation.get("RelationType")
            related_class_code = relation.get("RelatedClassCode")
            related_uri = relation.get("RelatedClassUri")
            if related_class_code:
                related_object = versioned_ns[_slug(str(related_class_code))]
            elif related_uri:
                related_object = URIRef(str(related_uri))
            else:
                continue

            if relation_type == "IsChildOf":
                graph.add((subject, RDFS.subClassOf, related_object))
                subclass_pairs.add((subject, related_object))
            elif relation_type:
                relation_predicate = META[_slug(str(relation_type))]
                graph.add((subject, relation_predicate, related_object))

        for class_property in item.get("ClassProperties", []):
            property_link = _resolve_class_property_link(
                property_lookup=property_lookup,
                class_item=item,
                class_property=class_property,
            )
            if property_link is None:
                unresolved_link_issues.append(
                    DictionaryLinkIssue(
                        class_code=_class_term_code(item, fallback=f"class_{index + 1}"),
                        class_property_code=_string_or_none(class_property.get("PropertyCode"))
                        or _string_or_none(class_property.get("Code")),
                        owned_uri=_string_or_none(class_property.get("OwnedUri")),
                        reason="unresolved",
                    )
                )
                continue
            if isinstance(property_link, DictionaryLinkIssue):
                ambiguous_link_issues.append(property_link)
                continue

            property_subject = property_link.subject
            graph.add((subject, META.hasProperty, property_subject))
            graph.add((property_subject, META.belongsToClass, subject))
            class_property_pairs.add((subject, property_subject))

            _add_literal(graph, property_subject, META.propertyCode, class_property.get("PropertyCode"))
            _add_literal(graph, property_subject, META.propertySet, class_property.get("PropertySet"))
            if property_link.resolution == "owned_uri":
                resolved_links_by_owned_uri += 1
            elif property_link.resolution == "code":
                resolved_links_by_code += 1
            elif property_link.resolution == "name":
                resolved_links_by_name += 1

    for index, item in enumerate(properties):
        subject = property_subjects[index]
        for relation in item.get("PropertyRelations", []):
            related_uri = relation.get("RelatedPropertyUri")
            if not related_uri:
                continue
            related_subjects = property_lookup["uri"].get(_normalized_key(related_uri), ())
            if len(related_subjects) == 1:
                graph.add((subject, META.relatedProperty, related_subjects[0].subject))
            else:
                graph.add((subject, META.relatedPropertyUri, Literal(str(related_uri))))

    return _DictionaryConversionContext(
        graph=graph,
        class_count=len(classes),
        subclass_count=len(subclass_pairs),
        property_count=len(properties),
        datatype_property_count=datatype_property_count,
        object_property_count=object_property_count,
        class_property_link_count=len(class_property_pairs),
        resolved_links_by_owned_uri=resolved_links_by_owned_uri,
        resolved_links_by_code=resolved_links_by_code,
        resolved_links_by_name=resolved_links_by_name,
        unresolved_link_issues=tuple(unresolved_link_issues),
        ambiguous_link_issues=tuple(ambiguous_link_issues),
    )


def _build_validation_report(
    *,
    payload: dict,
    context: _DictionaryConversionContext,
) -> DictionaryOntologyValidationReport:
    shape_issues = _validate_payload_shape(payload)
    class_field_issues = tuple(
        _collect_missing_fields(payload.get("Classes", []), REQUIRED_CLASS_FIELDS, _class_term_code, "class")
    )
    property_field_issues = tuple(
        _collect_missing_fields(
            payload.get("Properties", []), REQUIRED_PROPERTY_FIELDS, _property_term_code, "property"
        )
    )

    ttl_has_ontology = bool(list(context.graph.subjects(RDF.type, OWL.Ontology)))
    ttl_has_classes = (
        context.class_count == 0 or len(list(context.graph.subjects(RDF.type, OWL.Class))) >= context.class_count
    )
    ttl_has_properties = context.property_count == 0 or (
        len(list(context.graph.subjects(RDF.type, OWL.DatatypeProperty)))
        + len(list(context.graph.subjects(RDF.type, OWL.ObjectProperty)))
        >= context.property_count
    )
    ttl_has_class_property_links = context.class_property_link_count == 0 or (
        len(list(context.graph.triples((None, META.hasProperty, None)))) >= context.class_property_link_count
    )

    completeness_ok = (
        not class_field_issues
        and not property_field_issues
        and not context.unresolved_link_issues
        and not context.ambiguous_link_issues
    )
    ttl_form_ok = ttl_has_ontology and ttl_has_classes and ttl_has_properties and ttl_has_class_property_links
    shape_ok = not shape_issues
    issues: list[str] = list(shape_issues)
    if class_field_issues:
        issues.append(f"{len(class_field_issues)} classes are missing required fields")
    if property_field_issues:
        issues.append(f"{len(property_field_issues)} properties are missing required fields")
    if context.unresolved_link_issues:
        issues.append(f"{len(context.unresolved_link_issues)} class-property links could not be resolved")
    if context.ambiguous_link_issues:
        issues.append(f"{len(context.ambiguous_link_issues)} class-property links were ambiguous")
    if not ttl_has_ontology:
        issues.append("Converted TTL is missing owl:Ontology")
    if not ttl_has_classes:
        issues.append("Converted TTL is missing expected class declarations")
    if not ttl_has_properties:
        issues.append("Converted TTL is missing expected property declarations")
    if not ttl_has_class_property_links:
        issues.append("Converted TTL is missing expected class-property links")

    return DictionaryOntologyValidationReport(
        valid=shape_ok and completeness_ok and ttl_form_ok,
        shape_ok=shape_ok,
        completeness_ok=completeness_ok,
        ttl_form_ok=ttl_form_ok,
        source_dictionary_name=payload.get("DictionaryName"),
        source_dictionary_code=payload.get("DictionaryCode"),
        source_dictionary_version=payload.get("DictionaryVersion"),
        class_count=context.class_count,
        subclass_count=context.subclass_count,
        property_count=context.property_count,
        datatype_property_count=context.datatype_property_count,
        object_property_count=context.object_property_count,
        class_property_link_count=context.class_property_link_count,
        resolved_links_by_owned_uri=context.resolved_links_by_owned_uri,
        resolved_links_by_code=context.resolved_links_by_code,
        resolved_links_by_name=context.resolved_links_by_name,
        unresolved_link_count=len(context.unresolved_link_issues),
        ambiguous_link_count=len(context.ambiguous_link_issues),
        classes_missing_required_fields=class_field_issues,
        properties_missing_required_fields=property_field_issues,
        unresolved_class_property_links=context.unresolved_link_issues,
        ambiguous_class_property_links=context.ambiguous_link_issues,
        ttl_has_ontology=ttl_has_ontology,
        ttl_has_classes=ttl_has_classes,
        ttl_has_properties=ttl_has_properties,
        ttl_has_class_property_links=ttl_has_class_property_links,
        issues=tuple(issues),
    )


def _validate_payload_shape(payload: dict) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        return ["dictionary payload must be a JSON object"]
    if not isinstance(payload.get("Classes", []), list):
        issues.append("Classes must be a JSON list")
    if not isinstance(payload.get("Properties", []), list):
        issues.append("Properties must be a JSON list")
    return issues


def _collect_missing_fields(
    items: list[dict],
    required_fields: tuple[str, ...],
    code_getter,
    fallback_prefix: str,
) -> list[DictionaryFieldIssue]:
    issues: list[DictionaryFieldIssue] = []
    for index, item in enumerate(items):
        missing = tuple(field for field in required_fields if not _has_required_value(item, field))
        if missing:
            issues.append(
                DictionaryFieldIssue(
                    item_code=code_getter(item, fallback=f"{fallback_prefix}_{index + 1}"),
                    missing_fields=missing,
                )
            )
    return issues


def _has_required_value(item: dict, field_name: str) -> bool:
    if field_name not in item:
        return False
    value = item.get(field_name)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _build_property_lookup(
    properties: list[dict], property_subjects: dict[int, URIRef]
) -> dict[str, dict[str, tuple[_ResolvedPropertyLink, ...]]]:
    lookup = {
        "uid": {},
        "code": {},
        "name": {},
        "uri": {},
    }
    for index, item in enumerate(properties):
        resolved = _ResolvedPropertyLink(
            property_item=item,
            subject=property_subjects[index],
            resolution="",
        )
        _append_lookup_value(lookup["uid"], item.get("Uid"), resolved)
        _append_lookup_value(lookup["code"], item.get("Code"), resolved)
        _append_lookup_value(lookup["name"], item.get("Name"), resolved)
        _append_lookup_value(lookup["uri"], item.get("PropertyUri"), resolved)
    return lookup


def _append_lookup_value(
    bucket: dict[str, tuple[_ResolvedPropertyLink, ...]], raw_key: str | None, value: _ResolvedPropertyLink
) -> None:
    key = _normalized_key(raw_key)
    if key is None:
        return
    bucket[key] = (*bucket.get(key, ()), value)


def _resolve_class_property_link(
    *,
    property_lookup: dict[str, dict[str, tuple[_ResolvedPropertyLink, ...]]],
    class_item: dict,
    class_property: dict,
) -> _ResolvedPropertyLink | DictionaryLinkIssue | None:
    owned_uri = _normalized_key(class_property.get("OwnedUri"))
    property_code = _normalized_key(class_property.get("PropertyCode") or class_property.get("Code"))

    for resolution, key_name, raw_key in (
        ("owned_uri", "uid", owned_uri),
        ("code", "code", property_code),
        ("name", "name", property_code),
    ):
        if raw_key is None:
            continue
        candidates = property_lookup[key_name].get(raw_key, ())
        if len(candidates) == 1:
            candidate = candidates[0]
            return _ResolvedPropertyLink(
                property_item=candidate.property_item,
                subject=candidate.subject,
                resolution=resolution,
            )
        if len(candidates) > 1:
            return DictionaryLinkIssue(
                class_code=_class_term_code(class_item, fallback="class"),
                class_property_code=_string_or_none(class_property.get("PropertyCode"))
                or _string_or_none(class_property.get("Code")),
                owned_uri=_string_or_none(class_property.get("OwnedUri")),
                reason=f"ambiguous_{resolution}",
                candidate_property_codes=tuple(
                    sorted(
                        {_property_term_code(candidate.property_item, fallback="property") for candidate in candidates}
                    )
                ),
            )
    return None


def _class_term_code(item: dict, *, fallback: str) -> str:
    for key in ("Code", "Name", "Uid"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _property_term_code(item: dict, *, fallback: str) -> str:
    for key in ("Code", "PropertyCode", "Name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    property_uri = _string_or_none(item.get("PropertyUri"))
    if property_uri:
        return property_uri.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    uid = _string_or_none(item.get("Uid"))
    if uid:
        return uid
    return fallback


def _normalized_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized.casefold()


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
    property_type = str(item.get("PropertyType") or "").strip().lower()
    description = str(item.get("Description") or "").lower()
    if property_value_kind in {"complex", "reference"}:
        return OWL.ObjectProperty
    if property_type in {"object", "reference"}:
        return OWL.ObjectProperty
    if "reference to ifc" in description:
        return OWL.ObjectProperty
    return OWL.DatatypeProperty


def _add_literal(graph: Graph, subject: URIRef, predicate: URIRef, value: str | None) -> None:
    if value:
        graph.add((subject, predicate, Literal(value)))
