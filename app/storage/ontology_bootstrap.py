from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.schemas.ontologies import OntologyPublishRequest, OntologyReleaseStatus
from app.storage.graph_store import GraphStoreError
from app.storage.ontology_repository import OntologyManifestNotFoundError, OntologyRepository

logger = logging.getLogger(__name__)

DEFAULT_ONTOLOGY_RELEASE_VERSION = "2026.04.23"
DEFAULT_ONTOLOGY_RELEASE_DATE = date(2026, 4, 23)


@dataclass(frozen=True)
class OntologyBootstrapSpec:
    ontology_family: str
    country: str
    resource_filename: str
    version: str = DEFAULT_ONTOLOGY_RELEASE_VERSION
    status: OntologyReleaseStatus = OntologyReleaseStatus.draft
    issued_at: date = DEFAULT_ONTOLOGY_RELEASE_DATE
    default_version: bool = True


DEFAULT_ONTOLOGY_BOOTSTRAP_SPECS: tuple[OntologyBootstrapSpec, ...] = (
    OntologyBootstrapSpec(ontology_family="fbo", country="dk", resource_filename="fbo-dk.ttl"),
    OntologyBootstrapSpec(ontology_family="fbo", country="be", resource_filename="fbo-be.ttl"),
    OntologyBootstrapSpec(ontology_family="fbo", country="lt", resource_filename="fbo-lt.ttl"),
    OntologyBootstrapSpec(ontology_family="fbo", country="nl", resource_filename="fbo-nl.ttl"),
    OntologyBootstrapSpec(ontology_family="fbo", country="pt", resource_filename="fbo-pt.ttl"),
)


def bootstrap_default_ontologies(
    repo: OntologyRepository,
    specs: tuple[OntologyBootstrapSpec, ...] = DEFAULT_ONTOLOGY_BOOTSTRAP_SPECS,
) -> list[dict[str, str | bool]]:
    results: list[dict[str, str | bool]] = []

    for spec in specs:
        results.append(_bootstrap_single_ontology(repo, spec))

    return results


def _bootstrap_single_ontology(repo: OntologyRepository, spec: OntologyBootstrapSpec) -> dict[str, str | bool]:
    try:
        manifest = repo._load_manifest(spec.ontology_family, spec.country)
    except OntologyManifestNotFoundError:
        manifest = None
    except GraphStoreError:
        logger.exception(
            "Ontology bootstrap failed while loading manifest for %s/%s",
            spec.ontology_family,
            spec.country,
        )
        raise

    ttl = repo._load_local_resource(spec.resource_filename)
    metadata = repo._extract_ttl_metadata(ttl)

    if manifest is not None:
        existing_entry = repo._find_version_entry(manifest.get("versions", []), spec.version)
        if existing_entry is not None:
            same_payload = existing_entry.get("sha256") == metadata["sha256"]
            same_default = manifest.get("default_version") == spec.version
            same_status = existing_entry.get("status") == spec.status.value
            if same_payload and same_default and same_status:
                return {
                    "ontology_family": spec.ontology_family,
                    "country": spec.country,
                    "version": spec.version,
                    "resource_filename": spec.resource_filename,
                    "seeded": False,
                    "reason": "already_current",
                }

    published = repo.publish_version(
        OntologyPublishRequest(
            ontology_family=spec.ontology_family,
            country=spec.country,
            version=spec.version,
            status=spec.status,
            issued_at=spec.issued_at,
            resource_filename=spec.resource_filename,
            default_version=spec.default_version,
        )
    )
    return {
        "ontology_family": spec.ontology_family,
        "country": spec.country,
        "version": spec.version,
        "resource_filename": spec.resource_filename,
        "seeded": True,
        "reason": "published" if not published["overwritten"] else "updated",
    }


__all__ = [
    "DEFAULT_ONTOLOGY_BOOTSTRAP_SPECS",
    "DEFAULT_ONTOLOGY_RELEASE_DATE",
    "DEFAULT_ONTOLOGY_RELEASE_VERSION",
    "OntologyBootstrapSpec",
    "bootstrap_default_ontologies",
]
