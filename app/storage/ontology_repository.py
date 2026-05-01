from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic

from botocore.exceptions import BotoCoreError, ClientError
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from app.core.observability import is_request_profiling_enabled, log_event, record_metric
from app.schemas.ontologies import (
    OntologyPublishRequest,
    OntologyReleaseStatus,
    OntologyVersionFetchResponse,
    OntologyVersionListResponse,
    OntologyVersionMetadata,
)
from app.storage.graph_store import GraphStoreClient, GraphStoreError, GraphStoreNotFoundError

ONTOLOGIES_DATASET = "ontologies"
ONTOLOGY_DISTRIBUTION_BASE_URI = "https://api.firebim.org/ontologies/"
CANONICAL_ONTOLOGY_BASE_URI = "https://firebim.org/ontologies/"
MANIFEST_ROOT_PREFIX = "ontologies/manifests"
LOCAL_RESOURCE_DIRECTORIES = (
    Path(__file__).resolve().parents[1] / "ontologies" / "resources",
    Path(__file__).resolve().parents[1] / "lexicon" / "resources",
)
logger = logging.getLogger(__name__)


class OntologyManifestNotFoundError(GraphStoreNotFoundError):
    pass


class OntologyRepository:
    def __init__(self, client: GraphStoreClient | None = None) -> None:
        self.client = client or GraphStoreClient()
        self.dataset = ONTOLOGIES_DATASET
        self.distribution_base_uri = ONTOLOGY_DISTRIBUTION_BASE_URI
        self.canonical_base_uri = CANONICAL_ONTOLOGY_BASE_URI

    def health(self) -> bool:
        return self.client.ping_dataset(self.dataset)

    def resource_directories(self) -> list[str]:
        return [str(path) for path in LOCAL_RESOURCE_DIRECTORIES if path.exists()]

    def canonical_family_uri(self, ontology_family: str) -> str:
        return f"{self.canonical_base_uri}{ontology_family}#"

    def canonical_country_uri(self, ontology_family: str, country: str) -> str:
        return f"{self.canonical_base_uri}{ontology_family}/{country}#"

    def distribution_graph_uri(self, ontology_family: str, country: str, version: str) -> str:
        return f"{self.distribution_base_uri}{ontology_family}/{country}/versions/{version}"

    def list_versions(self, ontology_family: str, country: str) -> OntologyVersionListResponse:
        manifest = self._load_manifest(ontology_family, country)
        versions = self._version_models_from_manifest(manifest, ontology_family, country)
        return OntologyVersionListResponse(
            ontology_family=ontology_family,
            country=country,
            canonical_family_uri=self.canonical_family_uri(ontology_family),
            canonical_country_uri=self.canonical_country_uri(ontology_family, country),
            versions=versions,
        )

    def get_summary(self, ontology_family: str, country: str) -> dict:
        manifest = self._load_manifest(ontology_family, country)
        versions = manifest.get("versions", [])
        latest_version = self._latest_version(versions)
        stable_version = self._latest_stable_version(versions)
        default_version = manifest.get("default_version") or stable_version or latest_version
        base_path = f"/ontologies/{ontology_family}/{country}"
        return {
            "ontology_family": ontology_family,
            "country": country,
            "canonical_family_uri": self.canonical_family_uri(ontology_family),
            "canonical_country_uri": self.canonical_country_uri(ontology_family, country),
            "latest_version": latest_version,
            "latest_stable_version": stable_version,
            "default_version": default_version,
            "available_versions_url": f"{base_path}/versions",
            "latest_url": f"{base_path}/versions/latest",
            "stable_url": f"{base_path}/versions/stable",
        }

    def fetch_version(self, ontology_family: str, country: str, version: str) -> OntologyVersionFetchResponse:
        started_at = monotonic()
        manifest = self._load_manifest(ontology_family, country)
        resolved_version = self._resolve_version_token(version, manifest.get("versions", []))
        entry = self._find_version_entry(manifest.get("versions", []), resolved_version)
        if entry is None:
            raise GraphStoreNotFoundError(f"Ontology version not found: {ontology_family}/{country}/{resolved_version}")
        ttl = self.client.get_graph_ttl(self.dataset, entry["graph_uri"])
        versions = self._version_models_from_manifest(manifest, ontology_family, country)
        current = next(item for item in versions if item.version == resolved_version)
        duration_ms = round((monotonic() - started_at) * 1000, 3)
        record_metric("ontology_repo.fetch_version_ms", duration_ms, append=True)
        if is_request_profiling_enabled():
            log_event(
                logger,
                "ontology_repo.fetch_version",
                ontology_family=ontology_family,
                country=country,
                requested_version=version,
                resolved_version=resolved_version,
                duration_ms=duration_ms,
                byte_size=current.byte_size,
            )
        return OntologyVersionFetchResponse(
            ontology_family=ontology_family,
            country=country,
            canonical_family_uri=self.canonical_family_uri(ontology_family),
            canonical_country_uri=self.canonical_country_uri(ontology_family, country),
            version=current,
            ttl=ttl,
        )

    def fetch_version_metadata(self, ontology_family: str, country: str, version: str) -> OntologyVersionMetadata:
        started_at = monotonic()
        manifest = self._load_manifest(ontology_family, country)
        resolved_version = self._resolve_version_token(version, manifest.get("versions", []))
        versions = self._version_models_from_manifest(manifest, ontology_family, country)
        current = next((item for item in versions if item.version == resolved_version), None)
        if current is None:
            raise GraphStoreNotFoundError(f"Ontology version not found: {ontology_family}/{country}/{resolved_version}")
        duration_ms = round((monotonic() - started_at) * 1000, 3)
        record_metric("ontology_repo.fetch_version_metadata_ms", duration_ms, append=True)
        if is_request_profiling_enabled():
            log_event(
                logger,
                "ontology_repo.fetch_version_metadata",
                ontology_family=ontology_family,
                country=country,
                requested_version=version,
                resolved_version=resolved_version,
                duration_ms=duration_ms,
            )
        return current

    def publish_version(self, payload: OntologyPublishRequest) -> dict:
        ttl, resource_filename = self._resolve_ttl_payload(payload)
        metadata = self._extract_ttl_metadata(ttl)
        graph_uri = self.distribution_graph_uri(payload.ontology_family, payload.country, payload.version)
        overwritten = self.exists(graph_uri)

        manifest = self._load_manifest_or_default(payload.ontology_family, payload.country)
        version_entry = {
            "version": payload.version,
            "status": payload.status.value,
            "issued_at": payload.issued_at.isoformat(),
            "graph_uri": graph_uri,
            "ontology_uri": metadata["ontology_uri"],
            "version_iri": metadata["version_iri"],
            "label": metadata["label"],
            "description": metadata["description"],
            "triple_count": metadata["triple_count"],
            "class_count": metadata["class_count"],
            "property_count": metadata["property_count"],
            "property_set_count": metadata["property_set_count"],
            "subclass_count": metadata["subclass_count"],
            "class_property_link_count": metadata["class_property_link_count"],
            "byte_size": metadata["byte_size"],
            "sha256": metadata["sha256"],
            "imports": metadata["imports"],
            "resource_filename": resource_filename,
            "published_at": datetime.now(UTC).isoformat(),
        }
        manifest["versions"] = [item for item in manifest.get("versions", []) if item.get("version") != payload.version]
        manifest["versions"].append(version_entry)
        manifest["versions"] = self._sorted_version_entries(manifest["versions"])

        latest_stable_version = self._latest_stable_version(manifest["versions"])
        latest_version = self._latest_version(manifest["versions"])
        if payload.default_version:
            manifest["default_version"] = payload.version
        else:
            manifest["default_version"] = manifest.get("default_version") or latest_stable_version or latest_version

        self.client.put_graph_ttl(self.dataset, graph_uri, ttl)
        self._save_manifest(payload.ontology_family, payload.country, manifest)

        return {
            "published": True,
            "ontology_family": payload.ontology_family,
            "country": payload.country,
            "version": payload.version,
            "status": payload.status,
            "graph_uri": graph_uri,
            "ontology_uri": metadata["ontology_uri"],
            "canonical_family_uri": self.canonical_family_uri(payload.ontology_family),
            "canonical_country_uri": self.canonical_country_uri(payload.ontology_family, payload.country),
            "default_version": manifest["default_version"],
            "overwritten": overwritten,
            "triple_count": metadata["triple_count"],
            "byte_size": metadata["byte_size"],
            "resource_filename": resource_filename,
        }

    def delete_version(self, ontology_family: str, country: str, version: str) -> str:
        manifest = self._load_manifest(ontology_family, country)
        entry = self._find_version_entry(manifest.get("versions", []), version)
        if entry is None:
            raise GraphStoreNotFoundError(f"Ontology version not found: {ontology_family}/{country}/{version}")

        self.client.delete_graph(self.dataset, entry["graph_uri"])
        remaining_versions = [item for item in manifest.get("versions", []) if item.get("version") != version]
        manifest["versions"] = remaining_versions
        latest_stable_version = self._latest_stable_version(remaining_versions)
        latest_version = self._latest_version(remaining_versions)
        manifest["default_version"] = latest_stable_version or latest_version
        self._save_manifest(ontology_family, country, manifest)
        return entry["graph_uri"]

    def exists(self, graph_uri: str) -> bool:
        try:
            ttl = self.client.get_graph_ttl(self.dataset, graph_uri)
            return bool(ttl.strip())
        except GraphStoreNotFoundError:
            return False

    def _resolve_ttl_payload(self, payload: OntologyPublishRequest) -> tuple[str, str | None]:
        if payload.ttl:
            return payload.ttl, payload.resource_filename
        if payload.resource_filename:
            return self.load_local_resource(payload.resource_filename), payload.resource_filename
        raise ValueError("Either ttl or resource_filename must be provided")

    def load_local_resource(self, filename: str) -> str:
        for directory in LOCAL_RESOURCE_DIRECTORIES:
            candidate = directory / filename
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        raise ValueError(f"Local ontology resource not found: {filename}")

    def save_local_resource(self, filename: str, content: str) -> str:
        target_directory = LOCAL_RESOURCE_DIRECTORIES[-1]
        target_directory.mkdir(parents=True, exist_ok=True)
        target_path = target_directory / filename
        target_path.write_text(content, encoding="utf-8")
        return str(target_path)

    def _manifest_key(self, ontology_family: str, country: str) -> str:
        return f"{MANIFEST_ROOT_PREFIX}/{ontology_family}/{country}.json"

    def load_manifest(self, ontology_family: str, country: str) -> dict:
        return self._load_manifest(ontology_family, country)

    def find_version_entry(self, versions: list[dict], version: str) -> dict | None:
        return self._find_version_entry(versions, version)

    def extract_ttl_metadata(self, ttl: str) -> dict:
        return self._extract_ttl_metadata(ttl)

    def _load_manifest_or_default(self, ontology_family: str, country: str) -> dict:
        try:
            return self._load_manifest(ontology_family, country)
        except OntologyManifestNotFoundError:
            return {
                "ontology_family": ontology_family,
                "country": country,
                "default_version": None,
                "versions": [],
            }

    def _load_manifest(self, ontology_family: str, country: str) -> dict:
        key = self._manifest_key(ontology_family, country)
        try:
            response = self.client.client.get_object(Bucket=self.client.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise OntologyManifestNotFoundError(
                    f"Ontology manifest not found: {ontology_family}/{country}"
                ) from exc
            raise GraphStoreError(f"Ontology manifest fetch failed: {exc}") from exc
        except BotoCoreError as exc:
            raise GraphStoreError(f"Ontology manifest fetch failed: {exc}") from exc

        body = response["Body"].read().decode("utf-8")
        manifest = json.loads(body)
        manifest.setdefault("versions", [])
        manifest.setdefault("default_version", None)
        return manifest

    def _save_manifest(self, ontology_family: str, country: str, manifest: dict) -> None:
        key = self._manifest_key(ontology_family, country)
        payload = json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
        try:
            self.client.client.put_object(
                Bucket=self.client.bucket_name,
                Key=key,
                Body=payload,
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as exc:
            raise GraphStoreError(f"Ontology manifest save failed: {exc}") from exc

    def _extract_ttl_metadata(self, ttl: str) -> dict:
        graph = Graph()
        try:
            graph.parse(data=ttl, format="turtle")
        except Exception as exc:
            raise ValueError(f"Invalid Turtle content: {exc}") from exc

        ontology_uri = None
        version_iri = None
        label = None
        description = None
        imports: list[str] = []
        class_uris: set[str] = set()
        property_uris: set[str] = set()
        property_set_count = 0
        subclass_count = 0
        class_property_link_count = 0

        ontology_subjects = list(graph.subjects(RDF.type, OWL.Ontology))
        if ontology_subjects:
            subject = ontology_subjects[0]
            ontology_uri = str(subject)
            version_value = graph.value(subject, OWL.versionIRI)
            if version_value is not None:
                version_iri = str(version_value)
            label_value = graph.value(subject, RDFS.label)
            if label_value is not None:
                label = str(label_value)
            description_value = graph.value(subject, RDFS.comment)
            if description_value is not None:
                description = str(description_value)
            imports = sorted({str(item) for item in graph.objects(subject, OWL.imports)})

        class_uris = {str(subject) for subject in graph.subjects(RDF.type, OWL.Class)} | {
            str(subject) for subject in graph.subjects(RDF.type, RDFS.Class)
        }
        property_uris = (
            {str(subject) for subject in graph.subjects(RDF.type, RDF.Property)}
            | {str(subject) for subject in graph.subjects(RDF.type, OWL.ObjectProperty)}
            | {str(subject) for subject in graph.subjects(RDF.type, OWL.DatatypeProperty)}
        )
        group_predicate = URIRef("https://firebim.org/ontology-meta#groupOfProperties")
        has_property_predicate = URIRef("https://firebim.org/ontology-meta#hasProperty")
        property_set_count = sum(
            1
            for class_uri in class_uris
            if str(graph.value(URIRef(class_uri), group_predicate, default="")).strip().lower() == "true"
        )
        subclass_count = sum(1 for _ in graph.triples((None, RDFS.subClassOf, None)))
        class_property_link_count = sum(1 for _ in graph.triples((None, has_property_predicate, None)))

        encoded = ttl.encode("utf-8")
        return {
            "ontology_uri": ontology_uri,
            "version_iri": version_iri,
            "label": label,
            "description": description,
            "imports": imports,
            "triple_count": len(graph),
            "class_count": len(class_uris),
            "property_count": len(property_uris),
            "property_set_count": property_set_count,
            "subclass_count": subclass_count,
            "class_property_link_count": class_property_link_count,
            "byte_size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }

    def _version_models_from_manifest(
        self,
        manifest: dict,
        ontology_family: str,
        country: str,
    ) -> list[OntologyVersionMetadata]:
        latest_version = self._latest_version(manifest.get("versions", []))
        stable_version = self._latest_stable_version(manifest.get("versions", []))
        default_version = manifest.get("default_version") or stable_version or latest_version
        items: list[OntologyVersionMetadata] = []
        for entry in self._sorted_version_entries(manifest.get("versions", [])):
            version = str(entry["version"])
            items.append(
                OntologyVersionMetadata(
                    version=version,
                    status=OntologyReleaseStatus(str(entry["status"])),
                    issued_at=date.fromisoformat(str(entry["issued_at"])),
                    is_latest=version == latest_version,
                    is_stable=version == stable_version,
                    is_default=version == default_version,
                    url=f"/ontologies/{ontology_family}/{country}/versions/{version}",
                    ttl_url=f"/ontologies/{ontology_family}/{country}/versions/{version}/ttl",
                    graph_uri=str(entry["graph_uri"]),
                    ontology_uri=entry.get("ontology_uri"),
                    version_iri=entry.get("version_iri"),
                    label=entry.get("label"),
                    description=entry.get("description"),
                    triple_count=int(entry.get("triple_count", 0)),
                    class_count=int(entry.get("class_count", 0)),
                    property_count=int(entry.get("property_count", 0)),
                    property_set_count=int(entry.get("property_set_count", 0)),
                    subclass_count=int(entry.get("subclass_count", 0)),
                    class_property_link_count=int(entry.get("class_property_link_count", 0)),
                    byte_size=int(entry.get("byte_size", 0)),
                    sha256=str(entry.get("sha256", "")),
                    imports=[str(item) for item in entry.get("imports", [])],
                    resource_filename=entry.get("resource_filename"),
                    published_at=datetime.fromisoformat(str(entry["published_at"])),
                )
            )
        return items

    def _resolve_version_token(self, version: str, versions: list[dict]) -> str:
        if version == "latest":
            latest = self._latest_version(versions)
            if latest is None:
                raise GraphStoreNotFoundError("No ontology versions available")
            return latest
        if version == "stable":
            stable = self._latest_stable_version(versions)
            if stable is None:
                raise GraphStoreNotFoundError("No stable ontology version available")
            return stable
        return version

    def _find_version_entry(self, versions: list[dict], version: str) -> dict | None:
        for item in versions:
            if item.get("version") == version:
                return item
        return None

    def _latest_version(self, versions: list[dict]) -> str | None:
        sorted_versions = self._sorted_version_entries(versions)
        if not sorted_versions:
            return None
        return str(sorted_versions[0]["version"])

    def _latest_stable_version(self, versions: list[dict]) -> str | None:
        stable_versions = [item for item in versions if item.get("status") == OntologyReleaseStatus.stable.value]
        sorted_versions = self._sorted_version_entries(stable_versions)
        if not sorted_versions:
            return None
        return str(sorted_versions[0]["version"])

    def _sorted_version_entries(self, versions: list[dict]) -> list[dict]:
        def _version_key(item: dict) -> tuple[int, int, int]:
            raw = str(item.get("version", "0000.00"))
            parts = raw.split(".")
            if len(parts) == 2:
                year, month = parts
                day = "00"
            elif len(parts) == 3:
                year, month, day = parts
            else:
                return (0, 0, 0)
            return int(year), int(month), int(day)

        return sorted(versions, key=_version_key, reverse=True)


__all__ = [
    "CANONICAL_ONTOLOGY_BASE_URI",
    "LOCAL_RESOURCE_DIRECTORIES",
    "ONTOLOGIES_DATASET",
    "ONTOLOGY_DISTRIBUTION_BASE_URI",
    "OntologyManifestNotFoundError",
    "OntologyRepository",
]
