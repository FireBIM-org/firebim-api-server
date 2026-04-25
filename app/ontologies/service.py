from __future__ import annotations

from rdflib import RDF, Graph, URIRef
from rdflib.namespace import OWL, RDFS

from app.ontologies.dictionary_ontology import (
    DictionaryFieldIssue,
    DictionaryLinkIssue,
    DictionaryOntologyValidationReport,
    convert_dictionary_json_text,
    convert_dictionary_payload,
    normalize_dictionary_json,
)
from app.schemas.ontologies import (
    OntologyDictionaryCandidateCreateRequest,
    OntologyDictionaryConversionRequest,
    OntologyDictionaryFieldIssue,
    OntologyDictionaryLinkIssue,
    OntologyVersionFetchResponse,
    OntologyVersionSummaryResponse,
)
from app.schemas.ontologies import (
    OntologyDictionaryValidationReport as OntologyDictionaryValidationReportModel,
)
from app.storage.ontology_repository import OntologyRepository

ONTOLOGY_META_GROUP_OF_PROPERTIES_URI = "https://firebim.org/ontology-meta#groupOfProperties"
ONTOLOGY_META_HAS_PROPERTY_URI = "https://firebim.org/ontology-meta#hasProperty"


def fetch_ontology_version(
    repo: OntologyRepository,
    *,
    ontology_family: str,
    country: str,
    version: str,
) -> OntologyVersionFetchResponse:
    return repo.fetch_version(ontology_family.strip().lower(), country.strip().lower(), version.strip())


def build_dictionary_conversion(
    repo: OntologyRepository,
    payload: OntologyDictionaryConversionRequest,
):
    if payload.dictionary_json is not None:
        return convert_dictionary_payload(
            payload.dictionary_json,
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
        )
    if payload.source_resource_filename:
        source_text = repo.load_local_resource(payload.source_resource_filename)
        return convert_dictionary_json_text(
            source_text,
            ontology_family=payload.ontology_family,
            country=payload.country,
            version=payload.version,
        )
    raise ValueError("Either dictionary_json or source_resource_filename must be provided")


def resolve_candidate_dictionary_json(
    repo: OntologyRepository,
    payload: OntologyDictionaryCandidateCreateRequest,
) -> dict:
    if payload.dictionary_json is not None:
        return payload.dictionary_json
    if payload.source_resource_filename:
        return normalize_dictionary_json(repo.load_local_resource(payload.source_resource_filename))
    raise ValueError("Either dictionary_json or source_resource_filename must be provided")


def validation_report_model(
    report: DictionaryOntologyValidationReport,
) -> OntologyDictionaryValidationReportModel:
    return OntologyDictionaryValidationReportModel(
        valid=report.valid,
        shape_ok=report.shape_ok,
        completeness_ok=report.completeness_ok,
        ttl_form_ok=report.ttl_form_ok,
        source_dictionary_name=report.source_dictionary_name,
        source_dictionary_code=report.source_dictionary_code,
        source_dictionary_version=report.source_dictionary_version,
        class_count=report.class_count,
        subclass_count=report.subclass_count,
        property_count=report.property_count,
        datatype_property_count=report.datatype_property_count,
        object_property_count=report.object_property_count,
        class_property_link_count=report.class_property_link_count,
        resolved_links_by_owned_uri=report.resolved_links_by_owned_uri,
        resolved_links_by_code=report.resolved_links_by_code,
        resolved_links_by_name=report.resolved_links_by_name,
        unresolved_link_count=report.unresolved_link_count,
        ambiguous_link_count=report.ambiguous_link_count,
        classes_missing_required_fields=[field_issue_model(item) for item in report.classes_missing_required_fields],
        properties_missing_required_fields=[
            field_issue_model(item) for item in report.properties_missing_required_fields
        ],
        unresolved_class_property_links=[link_issue_model(item) for item in report.unresolved_class_property_links],
        ambiguous_class_property_links=[link_issue_model(item) for item in report.ambiguous_class_property_links],
        ttl_has_ontology=report.ttl_has_ontology,
        ttl_has_classes=report.ttl_has_classes,
        ttl_has_properties=report.ttl_has_properties,
        ttl_has_class_property_links=report.ttl_has_class_property_links,
        issues=list(report.issues),
    )


def summarize_ontology_ttl(ttl: str) -> dict[str, int]:
    graph = Graph()
    graph.parse(data=ttl, format="turtle")
    class_uris = {str(subject) for subject in graph.subjects(RDF.type, OWL.Class)} | {
        str(subject) for subject in graph.subjects(RDF.type, RDFS.Class)
    }
    property_uris = (
        {str(subject) for subject in graph.subjects(RDF.type, RDF.Property)}
        | {str(subject) for subject in graph.subjects(RDF.type, OWL.ObjectProperty)}
        | {str(subject) for subject in graph.subjects(RDF.type, OWL.DatatypeProperty)}
    )
    group_predicate = URIRef(ONTOLOGY_META_GROUP_OF_PROPERTIES_URI)
    has_property_predicate = URIRef(ONTOLOGY_META_HAS_PROPERTY_URI)
    property_set_count = sum(
        1
        for class_uri in class_uris
        if str(graph.value(URIRef(class_uri), group_predicate, default="")).strip().lower() == "true"
    )
    subclass_count = sum(1 for _ in graph.triples((None, RDFS.subClassOf, None)))
    class_property_link_count = sum(1 for _ in graph.triples((None, has_property_predicate, None)))
    return {
        "class_count": len(class_uris),
        "property_count": len(property_uris),
        "property_set_count": property_set_count,
        "subclass_count": subclass_count,
        "class_property_link_count": class_property_link_count,
    }


def build_version_summary_response(response: OntologyVersionFetchResponse) -> OntologyVersionSummaryResponse:
    summary = summarize_ontology_ttl(response.ttl)
    warnings: list[str] = []
    if summary["class_count"] == 0:
        warnings.append("Ontology has no declared classes.")
    if summary["property_count"] == 0:
        warnings.append("Ontology has no declared properties.")
    if response.version.triple_count <= 0:
        warnings.append("Ontology version has no triples.")
    return OntologyVersionSummaryResponse(
        ontology_family=response.ontology_family,
        country=response.country,
        version=response.version.version,
        ontology_uri=response.version.ontology_uri,
        version_iri=response.version.version_iri,
        title=(
            response.version.label
            or f"{response.ontology_family.upper()} {response.country.upper()} {response.version.version}"
        ),
        description=response.version.description,
        status=response.version.status,
        class_count=summary["class_count"],
        property_count=summary["property_count"],
        property_set_count=summary["property_set_count"],
        subclass_count=summary["subclass_count"],
        class_property_link_count=summary["class_property_link_count"],
        publish_ready=summary["class_count"] > 0 and summary["property_count"] > 0,
        warnings=warnings,
    )


def field_issue_model(issue: DictionaryFieldIssue) -> OntologyDictionaryFieldIssue:
    return OntologyDictionaryFieldIssue(item_code=issue.item_code, missing_fields=list(issue.missing_fields))


def link_issue_model(issue: DictionaryLinkIssue) -> OntologyDictionaryLinkIssue:
    return OntologyDictionaryLinkIssue(
        class_code=issue.class_code,
        class_property_code=issue.class_property_code,
        owned_uri=issue.owned_uri,
        reason=issue.reason,
        candidate_property_codes=list(issue.candidate_property_codes),
    )
