from __future__ import annotations

from types import SimpleNamespace

from rdflib import URIRef
from rdflib.namespace import OWL

from app.lexicon.ifc4_schema import SCHEMA_PROVENANCE
from app.lexicon.translation_validation import compile_translation_mappings, load_translation_modules
from app.models.models import User
from app.schemas.translation import TranslationStoredProfile, TranslationValidationDiagnostic
from app.schemas.translation_revision import (
    TranslationOntologyArtifact,
    TranslationPublishedRevision,
    TranslationRevisionContent,
)
from app.storage.ontology_repository import OntologyRepository
from app.storage.translation_profile_repository import TranslationProfileRepository


class TranslationPublicationValidationError(Exception):
    def __init__(self, diagnostics: list[TranslationValidationDiagnostic]):
        super().__init__("The saved draft cannot be published; resolve its validation diagnostics")
        self.diagnostics = diagnostics


def publish_translation_revision(
    profile_id: str,
    expected_generation: int,
    user: User | SimpleNamespace,
    repository: TranslationProfileRepository,
    ontology_repo: OntologyRepository,
) -> TranslationPublishedRevision:
    """Publish a validated immutable contract, independently of upload execution availability."""
    draft = repository.get_profile(profile_id, user)
    for reference in draft.revision_refs:
        if reference.draft_generation == expected_generation:
            return repository.get_revision(profile_id, reference.revision_id, user)
    repository.require_draft_generation(draft, expected_generation)
    content = prepare_translation_revision(draft, ontology_repo)
    return repository.commit_revision(content, user)


def prepare_translation_revision(
    draft: TranslationStoredProfile,
    ontology_repo: OntologyRepository,
) -> TranslationRevisionContent:
    """Revalidate once, retaining exactly the content used for compilation. Does not publish."""
    modules, diagnostics = load_translation_modules(draft.contract, ontology_repo)
    if not modules:
        raise TranslationPublicationValidationError(diagnostics)
    compiled, mapping_diagnostics = compile_translation_mappings(draft.contract, modules)
    diagnostics.extend(mapping_diagnostics)
    if not compiled or any(item.severity == "error" for item in diagnostics):
        raise TranslationPublicationValidationError(diagnostics)

    identities = {}
    for module in modules:
        key = f"{module.pin.family}/{module.pin.country}/{module.pin.version}"
        for iri in (module.pin.ontology_iri, module.pin.version_iri):
            identities[iri] = key
    pins = []
    artifacts = []
    for module in modules:
        dependencies = set(module.pin.dependencies)
        dependencies.update(
            identities[str(imported)]
            for imported in module.graph.objects(
                URIRef(module.pin.ontology_iri),
                OWL.imports,
            )
        )
        pin = module.pin.model_copy(
            update={
                "dependencies": sorted(dependencies),
                "artifact_ref": f"sha256:{module.pin.sha256}",
            },
            deep=True,
        )
        pins.append(pin)
        artifacts.append(TranslationOntologyArtifact(pin=pin, ttl=module.ttl))
    contract = draft.contract.model_copy(update={"destination_modules": pins}, deep=True)
    return TranslationRevisionContent(
        profile_id=draft.id,
        draft_generation=draft.generation,
        name=draft.name,
        description=draft.description,
        source_contract=draft.contract.model_copy(deep=True),
        contract=contract,
        compiled_mappings=compiled,
        ontology_artifacts=artifacts,
        diagnostics=[item for item in diagnostics if item.code != "ontology_retention_unavailable"],
        source_schema_provenance=SCHEMA_PROVENANCE,
    )
