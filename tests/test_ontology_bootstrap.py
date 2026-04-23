from __future__ import annotations

from datetime import date

from app.storage.ontology_bootstrap import (
    DEFAULT_ONTOLOGY_RELEASE_VERSION,
    OntologyBootstrapSpec,
    bootstrap_default_ontologies,
)
from app.storage.ontology_repository import OntologyManifestNotFoundError


class DummyBootstrapRepo:
    def __init__(self) -> None:
        self.manifests: dict[tuple[str, str], dict] = {}
        self.published_payloads: list[object] = []

    def _load_manifest(self, ontology_family: str, country: str) -> dict:
        key = (ontology_family, country)
        if key not in self.manifests:
            raise OntologyManifestNotFoundError("missing")
        return self.manifests[key]

    def _load_local_resource(self, filename: str) -> str:
        country = filename.removeprefix("fbo-").removesuffix(".ttl")
        return (
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
            f"<https://firebim.org/ontologies/fbo/{country}#> a owl:Ontology .\n"
        )

    def _extract_ttl_metadata(self, ttl: str) -> dict:
        return {
            "sha256": f"hash:{ttl}",
        }

    def _find_version_entry(self, versions: list[dict], version: str) -> dict | None:
        for item in versions:
            if item.get("version") == version:
                return item
        return None

    def publish_version(self, payload):
        self.published_payloads.append(payload)
        return {"overwritten": False}


def test_bootstrap_publishes_missing_default_ontologies():
    repo = DummyBootstrapRepo()

    results = bootstrap_default_ontologies(repo)

    assert len(results) == 5
    assert all(item["seeded"] is True for item in results)
    assert [payload.country for payload in repo.published_payloads] == ["dk", "be", "lt", "nl", "pt"]
    assert all(payload.version == DEFAULT_ONTOLOGY_RELEASE_VERSION for payload in repo.published_payloads)


def test_bootstrap_skips_current_release():
    repo = DummyBootstrapRepo()
    repo.manifests[("fbo", "be")] = {
        "default_version": DEFAULT_ONTOLOGY_RELEASE_VERSION,
        "versions": [
            {
                "version": DEFAULT_ONTOLOGY_RELEASE_VERSION,
                "status": "draft",
                "sha256": (
                    "hash:@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
                    "<https://firebim.org/ontologies/fbo/be#> a owl:Ontology .\n"
                ),
            }
        ],
    }

    results = bootstrap_default_ontologies(
        repo,
        specs=(
            OntologyBootstrapSpec(
                ontology_family="fbo",
                country="be",
                resource_filename="fbo-be.ttl",
                issued_at=date(2026, 4, 23),
            ),
        ),
    )

    assert results == [
        {
            "ontology_family": "fbo",
            "country": "be",
            "version": DEFAULT_ONTOLOGY_RELEASE_VERSION,
            "resource_filename": "fbo-be.ttl",
            "seeded": False,
            "reason": "already_current",
        }
    ]
    assert repo.published_payloads == []
