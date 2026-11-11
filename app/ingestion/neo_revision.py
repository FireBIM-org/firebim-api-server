"""Offline execution seam; callers must obtain revisions through authorized storage."""

import hashlib

from rdflib import Graph

from app.ingestion.neo_ifcowl_adapter import NativeSource, NeoAdapterError, adapt_ifcowl
from app.lexicon.ifc4_schema import SCHEMA_PROVENANCE
from app.lexicon.ifc_mapping_evaluator import EvaluationResult, evaluate_ifc_mappings
from app.schemas.translation_revision import TranslationPublishedRevision


def evaluate_neo_revision(
    revision: TranslationPublishedRevision,
    source_graph: Graph,
    model_id: str,
) -> tuple[NativeSource, EvaluationResult]:
    """Use retained compiled mappings, never a draft or the mutable ontology library."""
    verify_neo_revision(revision)
    content = revision.content
    adapted = adapt_ifcowl(source_graph, mappings=content.compiled_mappings)
    result = evaluate_ifc_mappings(
        content.compiled_mappings,
        adapted.records,
        model_id,
        model_units=adapted.model_units,
    )
    return adapted, result


def verify_neo_revision(revision: TranslationPublishedRevision) -> None:
    """Shared integrity checks for admission and execution, without compiling anything."""
    digest = revision.content.digest()
    if revision.content_digest != digest or revision.revision_id != f"ifctr_{digest}":
        raise NeoAdapterError("invalid_revision_digest", "Published revision content failed its integrity check")
    content = revision.content
    if content.source_schema_provenance != SCHEMA_PROVENANCE:
        raise NeoAdapterError("unsupported_source_contract", "Revision requires a different IFC4 source catalogue")
    if not content.compiled_mappings or any(item.severity == "error" for item in content.diagnostics):
        raise NeoAdapterError("invalid_revision_contract", "Expected nonempty, validated compiled mappings")
    if not content.ontology_artifacts or content.contract.destination_modules != [
        artifact.pin for artifact in content.ontology_artifacts
    ]:
        raise NeoAdapterError("invalid_ontology_retention", "Destination pins must match the retained artifacts")
    for artifact in content.ontology_artifacts:
        if hashlib.sha256(artifact.ttl.encode("utf-8")).hexdigest() != artifact.pin.sha256:
            raise NeoAdapterError("invalid_ontology_digest", "Retained ontology bytes failed their integrity check")
