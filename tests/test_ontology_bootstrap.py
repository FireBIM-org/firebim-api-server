from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_DB", "firebim")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_BUCKET_NAME", "firebim")
os.environ.setdefault("S3_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("S3_SECRET_ACCESS_KEY", "minioadmin")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

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

    def load_manifest(self, ontology_family: str, country: str) -> dict:
        return self._load_manifest(ontology_family, country)

    def _load_manifest(self, ontology_family: str, country: str) -> dict:
        key = (ontology_family, country)
        if key not in self.manifests:
            raise OntologyManifestNotFoundError("missing")
        return self.manifests[key]

    def load_local_resource(self, filename: str) -> str:
        if filename == "ifc-global.ttl":
            return (
                "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
                "@prefix ifc: <https://firebim.org/ontologies/ifc/global#> .\n"
                "<https://firebim.org/ontologies/ifc/global#> a owl:Ontology .\n"
                "ifc:IfcWall a owl:Class .\n"
            )
        if filename == "ifc-4.3.json":
            return (
                "{"
                '"DictionaryName":"IFC 4.3",'
                '"DictionaryVersion":"4.3",'
                '"Classes":[{"Code":"IfcWall","Name":"Wall","ClassType":"Class","ParentClassCode":"IfcBuiltElement"}],'
                '"Properties":[{"Code":"FireRating","Name":"Fire Rating","PropertyValueKind":"Single"}]'
                "}"
            )
        stem = filename.removesuffix(".ttl")
        _, _, country = stem.partition("-")
        return (
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
            f"<https://firebim.org/ontologies/fbo/{country}#> a owl:Ontology .\n"
        )

    def _extract_ttl_metadata(self, ttl: str) -> dict:
        return {
            "sha256": f"hash:{ttl}",
            "class_count": 1,
            "property_count": 0,
            "property_set_count": 0,
            "subclass_count": 0,
            "class_property_link_count": 0,
        }

    def extract_ttl_metadata(self, ttl: str) -> dict:
        return self._extract_ttl_metadata(ttl)

    def find_version_entry(self, versions: list[dict], version: str) -> dict | None:
        return self._find_version_entry(versions, version)

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

    assert len(results) == 7
    assert all(item["seeded"] is True for item in results)
    assert [payload.country for payload in repo.published_payloads] == [
        "dk",
        "be",
        "lt",
        "nl",
        "pt",
        "global",
        "global",
    ]
    assert [payload.ontology_family for payload in repo.published_payloads] == [
        "fbo",
        "fbo",
        "fbo",
        "fbo",
        "fbo",
        "rir",
        "ifc",
    ]
    assert [payload.version for payload in repo.published_payloads[:-2]] == [
        DEFAULT_ONTOLOGY_RELEASE_VERSION,
        DEFAULT_ONTOLOGY_RELEASE_VERSION,
        DEFAULT_ONTOLOGY_RELEASE_VERSION,
        DEFAULT_ONTOLOGY_RELEASE_VERSION,
        DEFAULT_ONTOLOGY_RELEASE_VERSION,
    ]
    assert repo.published_payloads[-2].version == "2026.03.25"
    assert repo.published_payloads[-1].version == DEFAULT_ONTOLOGY_RELEASE_VERSION
    assert repo.published_payloads[-1].ttl is not None
    assert repo.published_payloads[-1].resource_filename == "ifc-global.ttl"


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
                "class_count": 1,
                "property_count": 0,
                "property_set_count": 0,
                "subclass_count": 0,
                "class_property_link_count": 0,
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
