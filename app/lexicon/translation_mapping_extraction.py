from __future__ import annotations

import hashlib
import json
import re

from rdflib import BNode, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from app.lexicon.translation_validation import TranslationOntologyModule, load_translation_modules
from app.schemas.translation import (
    TranslationDraftMapping,
    TranslationMappingExtractionReport,
    TranslationStoredProfile,
    TranslationValidationDiagnostic,
)
from app.storage.ontology_repository import OntologyRepository

FBO = Namespace("https://firebim.org/ontologies/fbo#")
IFC = Namespace("https://firebim.org/ontologies/fbo/ifc#")
RANGE_PREDICATES = (RDFS.range, URIRef("http://schema.org/rangeIncludes"), URIRef("https://schema.org/rangeIncludes"))
TARGET_KINDS = {
    OWL.Class: "class",
    RDFS.Class: "class",
    OWL.DatatypeProperty: "datatype_property",
    OWL.ObjectProperty: "object_property",
}


def _rdf_value(value):
    if isinstance(value, Literal):
        return {
            "type": "literal",
            "value": str(value),
            "datatype": str(value.datatype) if value.datatype else None,
            "language": value.language,
        }
    return {"type": "blank_node" if isinstance(value, BNode) else "iri", "value": str(value)}


def _statements(graph, subject):
    statements = [
        {"predicate": str(predicate), "value": _rdf_value(value)}
        for predicate, value in graph.predicate_objects(subject)
    ]
    return sorted(statements, key=lambda statement: json.dumps(statement, sort_keys=True))


def _module_provenance(module: TranslationOntologyModule):
    return module.pin.model_dump(include={"family", "country", "version", "ontology_iri", "version_iri", "sha256"})


def _mapping_id(module: TranslationOntologyModule, declaration: URIRef, target: URIRef):
    identity = [module.pin.family, module.pin.country, module.pin.version, str(declaration), str(target)]
    return "module_" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def _origin_key(provenance):
    if not provenance or provenance.get("kind") != "ontology_module":
        return None
    module = provenance.get("module")
    if not isinstance(module, dict):
        return None
    parts = [module.get(field) for field in ("family", "country", "version")]
    parts.extend(provenance.get(field) for field in ("declaration_iri", "target_iri"))
    return tuple(parts) if all(isinstance(part, str) for part in parts) else None


def _suggestion(profile, module, declaration, target, target_modules):
    return snapshot_module_suggestion(profile.contract.source.schema_, module, declaration, target, target_modules)


def snapshot_module_suggestion(source_schema, module, declaration, target, target_modules):
    """Build the same verified-release snapshot for import and later semantic compilation."""
    graph = module.graph

    def raw(predicate):
        return sorted(str(value) for value in graph.objects(declaration, predicate))

    kinds = {
        TARGET_KINDS[kind]
        for owner in target_modules
        for kind in owner.graph.objects(target, RDF.type)
        if kind in TARGET_KINDS
    }
    ranges = sorted(
        {
            str(value)
            for owner in target_modules
            for predicate in RANGE_PREDICATES
            for value in owner.graph.objects(target, predicate)
        }
    )
    kind = next(iter(kinds)) if len(kinds) == 1 else None
    issues = ["source_schema_compatibility_unverified", "mapping_semantics_unavailable"]
    if kind is None:
        issues.append("mapping_target_kind_unresolved")
    if kind == "datatype_property" and (len(ranges) != 1 or not ranges[0].startswith(str(XSD))):
        issues.append("mapping_target_range_unresolved")
    classes = raw(IFC.hasClassPredefinedType)
    properties = raw(IFC.hasPsetProperty)
    if (
        not classes
        or any(
            not re.fullmatch(r"Ifc[A-Za-z0-9_]+(?:\.[A-Z0-9_]+)?(?:,\s*Ifc[A-Za-z0-9_]+(?:\.[A-Z0-9_]+)?)*", value)
            for value in classes
        )
        or any(not re.fullmatch(r"Pset_[A-Za-z0-9_]+\.[A-Za-z0-9_]+", value) for value in properties)
    ):
        issues.append("mapping_selector_unsupported")
    if any(graph.objects(declaration, IFC.hasInterpretationResult)) or any(
        graph.objects(declaration, IFC.hasArgumentation)
    ):
        issues.append("mapping_interpretation_required")
    if any(isinstance(value, BNode) for _, value in graph.predicate_objects(declaration)):
        issues.append("mapping_nested_declaration_unsupported")
    evidence = [
        {"predicate": str(predicate), "value": _rdf_value(value)}
        for predicate in (RDFS.label, RDFS.comment)
        for value in graph.objects(URIRef(module.pin.ontology_iri), predicate)
    ]
    return TranslationDraftMapping(
        id=_mapping_id(module, declaration, target),
        enabled=True,
        review_state="accepted",
        seed_provenance={
            "kind": "ontology_module",
            "module": _module_provenance(module),
            "declaration_iri": str(declaration),
            "target_iri": str(target),
            "target_modules": [_module_provenance(owner) for owner in target_modules],
            "source_schema_evidence": sorted(evidence, key=lambda item: json.dumps(item, sort_keys=True)),
        },
        definition={
            "language": "fbo-module-suggestion/1",
            "source": {
                "schema": source_schema,
                "class_predefined_types": classes,
                "pset_properties": properties,
                "data_types": raw(IFC.hasDataType),
                "values": raw(IFC.hasValue),
            },
            "target": {"iri": str(target), "kind": kind, "ranges": ranges},
            "declaration": {"iri": str(declaration), "statements": _statements(graph, declaration)},
            "review_issues": issues,
        },
    )


def extract_module_mappings(
    profile: TranslationStoredProfile,
    ontology_repo: OntologyRepository,
) -> TranslationMappingExtractionReport:
    modules, diagnostics = load_translation_modules(profile.contract, ontology_repo)
    report = TranslationMappingExtractionReport(
        profile_id=profile.id,
        generation=profile.generation,
        mappings=[],
        diagnostics=diagnostics,
    )
    if not modules:
        return report
    modules.sort(key=lambda module: (module.pin.family, module.pin.country, module.pin.version))
    existing = {mapping.id: mapping for mapping in profile.contract.mappings}
    existing_origins = {
        _origin_key(mapping.seed_provenance): mapping
        for mapping in profile.contract.mappings
        if _origin_key(mapping.seed_provenance) is not None
    }
    found = set()
    unselected = set()
    for module in modules:
        graph = module.graph
        pairs = set(graph.subject_objects(IFC.mapsToTerm))
        # Inverse links are authoritative even when the alignment omits mapsToTerm.
        for owner in modules:
            for target, declaration in owner.graph.subject_objects(FBO.hasIFCMapping):
                if any(
                    (declaration, predicate, None) in graph
                    for predicate in (
                        IFC.hasClassPredefinedType,
                        IFC.hasPsetProperty,
                        IFC.mapsToTerm,
                    )
                ):
                    pairs.add((declaration, target))
        for declaration, target in sorted(pairs, key=lambda pair: tuple(map(str, pair))):
            if not isinstance(declaration, URIRef) or not isinstance(target, URIRef):
                report.diagnostics.append(
                    TranslationValidationDiagnostic(
                        code="mapping_link_invalid",
                        severity="warning",
                        field_path="contract.destination_modules",
                        message="Mapping links require full declaration and target IRIs; "
                        "blank-node links are unresolved.",
                    )
                )
                continue
            owners = [owner for owner in modules if (target, None, None) in owner.graph]
            if not owners:
                unselected.add(str(target))
                continue
            mapping = _suggestion(profile, module, declaration, target, owners)
            if mapping.id in found:
                continue
            found.add(mapping.id)
            previous = existing.get(mapping.id) or existing_origins.get(_origin_key(mapping.seed_provenance))
            if previous is not None:
                report.skipped_existing += 1
                provenance = previous.seed_provenance or {}
                previous_module = provenance.get("module")
                if isinstance(previous_module, dict) and previous_module.get("sha256") != module.pin.sha256:
                    report.diagnostics.append(
                        TranslationValidationDiagnostic(
                            code="mapping_seed_changed",
                            severity="warning",
                            mapping_id=previous.id,
                            field_path="contract.mappings",
                            message="The module content changed under the same release. "
                            "Your existing mapping was preserved, not refreshed; review its provenance explicitly.",
                        )
                    )
                if provenance.get("target_modules") != mapping.seed_provenance["target_modules"]:
                    report.diagnostics.append(
                        TranslationValidationDiagnostic(
                            code="mapping_target_changed",
                            severity="warning",
                            mapping_id=previous.id,
                            field_path="contract.mappings",
                            message="The selected destination definitions differ from "
                            "the imported snapshot. Your mapping was preserved; "
                            "review the target provenance explicitly.",
                        )
                    )
                continue
            report.mappings.append(mapping)
    if unselected:
        report.diagnostics.append(
            TranslationValidationDiagnostic(
                code="mapping_target_not_selected",
                severity="info",
                field_path="contract.destination_modules",
                message=f"Skipped {len(unselected)} destination terms not defined in selected modules. "
                "Select their destination modules to import those mappings; local names are never matched.",
            )
        )
    for owner in modules:
        for target, declaration in sorted(
            owner.graph.subject_objects(FBO.hasIFCMapping), key=lambda pair: tuple(map(str, pair))
        ):
            if not any(
                (declaration, predicate, None) in module.graph
                for module in modules
                for predicate in (
                    IFC.hasClassPredefinedType,
                    IFC.hasPsetProperty,
                    IFC.mapsToTerm,
                )
            ):
                report.diagnostics.append(
                    TranslationValidationDiagnostic(
                        code="mapping_module_missing",
                        severity="warning",
                        field_path="contract.destination_modules",
                        message=f"{target} references {declaration}, but its IFC declaration is not loaded. "
                        "Select the mapping module (for FBO, fbo/ifc) at the same release. "
                        "Linked URLs are not fetched.",
                    )
                )
    if not found:
        report.diagnostics.append(
            TranslationValidationDiagnostic(
                code="module_mappings_not_found",
                severity="info",
                field_path="contract.destination_modules",
                message="No IFC mapping declarations for the selected destination terms were found.",
            )
        )
    else:
        report.diagnostics.append(
            TranslationValidationDiagnostic(
                code="module_mappings_require_review",
                severity="warning",
                field_path="contract.mappings",
                message="New module mappings are enabled by default, not validated. Save, then validate IFC4 "
                "compatibility and executable semantics. Unsupported mappings, including unresolved "
                "fire resistance, block validation until disabled or resolved. Existing choices are preserved.",
            )
        )
    return report
