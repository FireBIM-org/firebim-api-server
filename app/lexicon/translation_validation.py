from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF

from app.lexicon.ifc_mapping_validation import mapping_conflicts, validate_executable_mapping
from app.schemas.translation import (
    TranslationContract,
    TranslationOntologyPin,
    TranslationStoredProfile,
    TranslationValidationDiagnostic,
    TranslationValidationReport,
    TranslationValidationStep,
)
from app.storage.graph_store import GraphStoreError, GraphStoreNotFoundError
from app.storage.ontology_repository import OntologyRepository


@dataclass
class TranslationOntologyModule:
    pin: TranslationOntologyPin
    graph: Graph
    ttl: str | None = None


def _validate_destinations(
    contract: TranslationContract,
    ontology_repo: OntologyRepository,
    modules: list[TranslationOntologyModule] | None = None,
) -> list[TranslationValidationDiagnostic]:
    diagnostics = []
    if not contract.destination_modules:
        return [
            TranslationValidationDiagnostic(
                code="destination_modules_required",
                severity="error",
                field_path="contract.destination_modules",
                message="Select at least one destination module.",
            )
        ]

    seen = set()
    verified_modules = set()
    import_targets: dict[str, set[str]] = {}
    imports = []
    for index, pin in enumerate(contract.destination_modules):
        path = f"contract.destination_modules[{index}]"
        identity = (pin.family, pin.country)
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", part) for part in identity):
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="ontology_identity_invalid",
                    severity="error",
                    field_path=path,
                    message="Use a library family/module identity, without paths or URL characters.",
                )
            )
            continue
        if identity in seen:
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="destination_module_duplicate",
                    severity="error",
                    field_path=path,
                    message="A destination module can only be selected once.",
                )
            )
            continue
        seen.add(identity)
        if pin.version != contract.destination_release:
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="destination_release_mismatch",
                    severity="error",
                    field_path=f"{path}.version",
                    message=f"Select the agreed exact release {contract.destination_release}; aliases are not pins.",
                )
            )
            continue
        try:
            release = ontology_repo.fetch_version(pin.family, pin.country, pin.version)
        except GraphStoreNotFoundError:
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="ontology_release_not_found",
                    severity="error",
                    field_path=path,
                    message=f"Destination release {pin.family}/{pin.country}/{pin.version} was not found.",
                )
            )
            continue
        except GraphStoreError:
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="ontology_storage_unavailable",
                    severity="warning",
                    field_path=path,
                    message="Ontology storage is unavailable. Retry validation; this is not a mapping error.",
                )
            )
            continue

        metadata = release.version
        digest = hashlib.sha256(release.ttl.encode("utf-8")).hexdigest()
        if metadata.version != pin.version:
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="destination_release_mismatch",
                    severity="error",
                    field_path=f"{path}.version",
                    message="The repository returned a different release from the requested exact version.",
                )
            )
            continue
        if digest != metadata.sha256:
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="ontology_digest_mismatch",
                    severity="error",
                    field_path=f"{path}.sha256",
                    message="Ontology content does not match its release digest. The release cannot be verified.",
                )
            )
            continue
        try:
            graph = Graph().parse(data=release.ttl, format="turtle")
        except (SyntaxError, ValueError):
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="ontology_content_invalid",
                    severity="error",
                    field_path=path,
                    message="The destination release is not valid Turtle.",
                )
            )
            continue
        ontology = URIRef(metadata.ontology_uri or "")
        if (
            (ontology, RDF.type, OWL.Ontology) not in graph
            or not metadata.version_iri
            or set(graph.objects(ontology, OWL.versionIRI)) != {URIRef(metadata.version_iri)}
        ):
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="ontology_identity_mismatch",
                    severity="error",
                    field_path=path,
                    message="Ontology and version IRIs must be declared unambiguously in the release content "
                    "and match its metadata.",
                )
            )
            continue
        for field, actual in (
            ("ontology_iri", metadata.ontology_uri),
            ("version_iri", metadata.version_iri),
            ("sha256", digest),
        ):
            expected = getattr(pin, field)
            if expected is not None and expected != actual:
                diagnostics.append(
                    TranslationValidationDiagnostic(
                        code="ontology_pin_mismatch",
                        severity="error",
                        field_path=f"{path}.{field}",
                        message=f"Saved {field} does not match the current release. Do not silently replace the pin.",
                    )
                )
        if modules is not None:
            modules.append(
                TranslationOntologyModule(
                    pin=pin.model_copy(
                        update={
                            "ontology_iri": metadata.ontology_uri,
                            "version_iri": metadata.version_iri,
                            "sha256": digest,
                        }
                    ),
                    graph=graph,
                    ttl=release.ttl,
                )
            )
        module_key = f"{pin.family}/{pin.country}/{pin.version}"
        verified_modules.add(module_key)
        for iri in (metadata.ontology_uri, metadata.version_iri):
            import_targets.setdefault(iri, set()).add(module_key)
        imports.extend((path, imported) for imported in graph.objects(ontology, OWL.imports))
        diagnostics.append(
            TranslationValidationDiagnostic(
                code="ontology_retention_unavailable",
                severity="warning",
                field_path=path,
                message="Validation checks the currently stored release only. It does not retain an immutable "
                "artifact or save resolved pin metadata to this draft.",
            )
        )
    for index, pin in enumerate(contract.destination_modules):
        for dependency in pin.dependencies:
            if dependency not in verified_modules:
                diagnostics.append(
                    TranslationValidationDiagnostic(
                        code="ontology_dependency_missing",
                        severity="error",
                        field_path=f"contract.destination_modules[{index}].dependencies",
                        message=f"Select and verify the exact dependency module {dependency} (family/module/version).",
                    )
                )
    for path, imported in imports:
        if not isinstance(imported, URIRef) or len(import_targets.get(str(imported), set())) != 1:
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="ontology_import_unresolved",
                    severity="error",
                    field_path=f"{path}.dependencies",
                    message=f"Import {imported} must resolve unambiguously to a selected, verified exact release. "
                    "Validation does not fetch external import URLs.",
                )
            )
    return diagnostics


def load_translation_modules(
    contract: TranslationContract,
    ontology_repo: OntologyRepository,
) -> tuple[list[TranslationOntologyModule], list[TranslationValidationDiagnostic]]:
    """Load only selected releases, verifying the same bytes used for extraction."""
    modules = []
    diagnostics = _validate_destinations(contract, ontology_repo, modules)
    if any(item.severity == "error" or item.code == "ontology_storage_unavailable" for item in diagnostics):
        return [], diagnostics
    return modules, diagnostics


def compile_translation_mappings(contract: TranslationContract, modules: list[TranslationOntologyModule]):
    """Validate enabled definitions and return reference-executable mappings without changing the draft."""
    from app.lexicon.translation_mapping_compiler import compile_module_suggestion

    diagnostics = []
    compiled = {}
    seen = set()
    for index, mapping in enumerate(contract.mappings):
        path = f"contract.mappings[{index}]"
        if mapping.id in seen:
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="mapping_id_duplicate",
                    severity="error",
                    mapping_id=mapping.id,
                    field_path=f"{path}.id",
                    message="Mapping IDs must be unique, including disabled mappings.",
                )
            )
        seen.add(mapping.id)
        if not mapping.enabled:
            continue
        definition = mapping.definition
        if modules and definition.get("language") == "fbo-module-suggestion/1":
            definition, issues = compile_module_suggestion(mapping, modules, f"{path}.definition")
            diagnostics.extend(issues)
            if definition is None:
                continue
        if mapping.review_state != "accepted":
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="mapping_review_required",
                    severity="error",
                    mapping_id=mapping.id,
                    field_path=f"{path}.review_state",
                    message="Enabled mappings must be explicitly accepted. "
                    "Disable unresolved or rejected suggestions, including unresolved fire-resistance mappings.",
                )
            )
        if not mapping.definition:
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="mapping_definition_required",
                    severity="error",
                    mapping_id=mapping.id,
                    field_path=f"{path}.definition",
                    message="An enabled mapping needs a definition.",
                )
            )
        elif mapping.review_state == "accepted":
            if not modules:
                diagnostics.append(
                    TranslationValidationDiagnostic(
                        code="mapping_validation_unavailable",
                        severity="warning",
                        mapping_id=mapping.id,
                        field_path=f"{path}.definition",
                        message="Verify all selected destination releases before checking mapping semantics.",
                    )
                )
                continue
            executable, issues = validate_executable_mapping(definition, modules, mapping.id, f"{path}.definition")
            diagnostics.extend(issues)
            if executable is not None:
                compiled[mapping.id] = executable
    if not any(mapping.enabled for mapping in contract.mappings):
        count = len(contract.mappings)
        if count:
            message = f"{count} saved {'mapping is' if count == 1 else 'mappings are'} disabled. "
            if any(mapping.definition.get("language") == "fbo-module-suggestion/1" for mapping in contract.mappings):
                message += "Choose Enable all module mappings, then Save draft and Validate draft. "
            else:
                message += "Accept and enable a mapping, then Save draft and Validate draft. "
            message += "New import defaults do not change saved choices. Disabled definitions are not validated."
        else:
            message = (
                "No mappings are saved. Save module selections, wait for automatic mapping loading, "
                "then Save draft again before validating, or add an accepted, enabled mapping."
            )
        diagnostics.append(
            TranslationValidationDiagnostic(
                code="enabled_mappings_required",
                severity="error",
                field_path="contract.mappings",
                message=message,
            )
        )
    elif (
        not compiled
        and any(item.code == "mapping_excluded_by_agreement" for item in diagnostics)
        and not any(item.severity == "error" or item.code == "mapping_validation_unavailable" for item in diagnostics)
    ):
        diagnostics.append(
            TranslationValidationDiagnostic(
                code="executable_mappings_required",
                severity="error",
                field_path="contract.mappings",
                message="All enabled mappings are excluded by agreement. "
                "At least one accepted, enabled, executable mapping is required; "
                "exclusions are not successful mappings.",
            )
        )
    diagnostics.extend(mapping_conflicts(compiled))
    return ({} if any(item.severity == "error" for item in diagnostics) else compiled), diagnostics


def validate_translation_draft(
    profile: TranslationStoredProfile,
    ontology_repo: OntologyRepository,
) -> TranslationValidationReport:
    modules, diagnostics = load_translation_modules(profile.contract, ontology_repo)
    destination_status = "passed"
    if any(item.severity == "error" for item in diagnostics):
        destination_status = "failed"
    elif any(item.code == "ontology_storage_unavailable" for item in diagnostics):
        destination_status = "unavailable"
    _, mapping_diagnostics = compile_translation_mappings(profile.contract, modules)
    mapping_status = "passed"
    if any(item.severity == "error" for item in mapping_diagnostics):
        mapping_status = "failed"
    elif any(item.code == "mapping_validation_unavailable" for item in mapping_diagnostics):
        mapping_status = "unavailable"
    diagnostics.extend(mapping_diagnostics)
    diagnostics.append(
        TranslationValidationDiagnostic(
            code="translation_execution_unavailable",
            severity="info",
            field_path="publication",
            message="Production SQL/Ontop upload execution is not connected. This does not prevent publication "
            "of a validated immutable revision. Publication does not make it upload-ready or produce model results.",
        )
    )
    authoring_valid = destination_status == "passed" and mapping_status == "passed"
    publication_status = (
        "passed"
        if authoring_valid
        else ("failed" if "failed" in (destination_status, mapping_status) else "unavailable")
    )
    return TranslationValidationReport(
        profile_id=profile.id,
        generation=profile.generation,
        authoring_valid=authoring_valid,
        publish_ready=authoring_valid,
        steps=[
            TranslationValidationStep(
                id="destination_ontologies",
                status=destination_status,
                message="Check exact destination releases, content integrity and dependencies. "
                "A passed check is not immutable artifact retention.",
            ),
            TranslationValidationStep(
                id="mappings",
                status=mapping_status,
                message="Check accepted, enabled IFC4 mappings: exact source selectors, destination kinds/ranges, "
                "typed values, bounded conditions/transforms, relationship bindings and static conflicts. "
                "Data-dependent errors still require evaluation; this is not upload execution.",
            ),
            TranslationValidationStep(
                id="publication",
                status=publication_status,
                message="Publish a validated immutable revision after destination and mapping checks pass. "
                "Publication revalidates the saved generation and retains its content; upload execution is separate.",
            ),
        ],
        diagnostics=diagnostics,
    )
