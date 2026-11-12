"""Sync ontology releases from a public registry (firebim.org or any compatible index) into this API.

The registry format is the static JSON published at
https://firebim.org/documentation/ontologies/index.json:

    {
      "schema_version": 1,
      "registry": "...",
      "ontologies": [
        {
          "ontology_family": "fbo", "country": "dk", "kind": "ontology", "visibility": "public",
          "title": "...", "latest_version": "2026.09.04", "latest_stable_version": null,
          "default_version": "2026.09.04",
          "versions": [ {"version": "2026.09.04", "status": "draft", "issued_at": "2026-09-04",
                          "ttl_url": "https://.../fbo-dk.ttl", "sha256": "...", "byte_size": 1234,
                          "is_latest": true, "is_stable": false, "is_default": true}, ... ]
        }
      ]
    }

No credentials are needed to read the registry; publishing into this API requires an authenticated user.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.core.url_guard import UnsafeUrlError, check_url, fetch_public_url
from app.schemas.ontologies import (
    OntologyPublishRequest,
    OntologyReleaseStatus,
    OntologySyncAction,
    OntologySyncItem,
    OntologySyncRequest,
    OntologySyncResponse,
)
from app.storage.graph_store import GraphStoreError
from app.storage.ontology_repository import OntologyManifestNotFoundError, OntologyRepository

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSIONS = {1}

Fetcher = Callable[[str], bytes]


class OntologyRegistryError(ValueError):
    """The registry could not be fetched or is not in the expected format."""


class OntologySourceNotAllowedError(OntologyRegistryError):
    """A user-supplied source_url (or a URL listed in its index) points somewhere the server must not fetch."""


_USER_AGENT = {"User-Agent": "firebim-api-server/sync"}


def _default_fetcher(url: str) -> bytes:
    """Fetcher for the operator-configured registry (ONTOLOGY_REGISTRY_URL), which is trusted."""
    timeout = httpx.Timeout(settings.ONTOLOGY_SYNC_TIMEOUT_SECONDS)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=_USER_AGENT) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def _guarded_fetcher(url: str) -> bytes:
    """Fetcher for user-supplied registries: public http(s) hosts only, every redirect hop re-checked."""
    return fetch_public_url(
        url,
        timeout=settings.ONTOLOGY_SYNC_TIMEOUT_SECONDS,
        allowed_hosts=settings.ontology_sync_allowed_hosts,
        headers=_USER_AGENT,
    )


def is_configured_registry(source_url: str | None) -> bool:
    return not source_url or source_url == settings.ONTOLOGY_REGISTRY_URL


def ensure_source_allowed(request: OntologySyncRequest) -> None:
    """Cheap up-front check of a user-supplied source_url (no DNS); the fetch re-checks with DNS on every hop."""
    if is_configured_registry(request.source_url):
        return
    try:
        check_url(request.source_url or "", allowed_hosts=settings.ontology_sync_allowed_hosts)
    except UnsafeUrlError as exc:
        raise OntologySourceNotAllowedError(f"source_url is not allowed: {exc}") from exc


def fetch_registry(source_url: str, fetcher: Fetcher = _default_fetcher) -> dict[str, Any]:
    try:
        raw = fetcher(source_url)
    except UnsafeUrlError as exc:
        raise OntologySourceNotAllowedError(f"source_url is not allowed: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OntologyRegistryError(f"Could not fetch registry {source_url}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise OntologyRegistryError(f"Registry {source_url} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("ontologies"), list):
        raise OntologyRegistryError(f"Registry {source_url} does not contain an 'ontologies' list")
    schema_version = data.get("schema_version")
    if schema_version is not None and int(schema_version) not in SUPPORTED_SCHEMA_VERSIONS:
        raise OntologyRegistryError(f"Unsupported registry schema_version {schema_version}")
    return data


def _resolve_url(base_url: str | None, url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    if base_url:
        return f"{base_url.rstrip('/')}/{url.lstrip('/')}"
    return url


def _local_state(repo: OntologyRepository, family: str, country: str) -> tuple[dict | None, dict[str, dict]]:
    try:
        manifest = repo.load_manifest(family, country)
    except OntologyManifestNotFoundError:
        return None, {}
    entries = {str(item.get("version")): item for item in manifest.get("versions", [])}
    return manifest, entries


def build_sync_plan(
    repo: OntologyRepository,
    registry: dict[str, Any],
    request: OntologySyncRequest,
    *,
    source_url: str,
) -> list[OntologySyncItem]:
    """Compare the registry with the local manifests and decide what to do per version."""
    items: list[OntologySyncItem] = []
    selections = set(request.selections or [])
    base_url = registry.get("base_url")

    for entry in registry.get("ontologies", []):
        family = str(entry.get("ontology_family", "")).strip().lower()
        country = str(entry.get("country", "")).strip().lower()
        if not family or not country:
            continue
        visibility = str(entry.get("visibility", "public"))
        kind = str(entry.get("kind", "ontology"))
        title = entry.get("title")
        if not selections:
            if request.families and family not in request.families:
                continue
            if request.countries and country not in request.countries:
                continue
            if visibility != "public" and not request.include_unlisted:
                continue

        _manifest, local_versions = _local_state(repo, family, country)

        for version_entry in entry.get("versions", []):
            version = str(version_entry.get("version", "")).strip()
            item_id = f"{family}/{country}/{version}"
            if selections and item_id not in selections:
                continue
            is_latest = bool(version_entry.get("is_latest"))
            if not selections and request.latest_only and not is_latest:
                continue
            try:
                status = OntologyReleaseStatus(str(version_entry.get("status", "draft")))
            except ValueError:
                status = OntologyReleaseStatus.draft
            issued_raw = version_entry.get("issued_at")
            try:
                issued_at = date.fromisoformat(str(issued_raw)) if issued_raw else date.today()
            except ValueError:
                issued_at = date.today()
            ttl_url = _resolve_url(base_url, str(version_entry.get("ttl_url", "")))
            remote_sha = version_entry.get("sha256")
            local = local_versions.get(version)
            local_sha = local.get("sha256") if local else None
            local_status = None
            if local and local.get("status"):
                try:
                    local_status = OntologyReleaseStatus(str(local["status"]))
                except ValueError:
                    local_status = None

            if not ttl_url:
                action, reason = OntologySyncAction.skip, "registry entry has no ttl_url"
            elif not version:
                action, reason = OntologySyncAction.skip, "registry entry has no version"
            elif local is None:
                action, reason = OntologySyncAction.publish, "not present locally"
            elif remote_sha and local_sha and remote_sha != local_sha:
                if request.overwrite:
                    action, reason = OntologySyncAction.update, "content differs (sha256) – overwrite requested"
                else:
                    action, reason = OntologySyncAction.skip, "content differs (sha256); set overwrite=true to replace"
            elif local_status is not None and local_status != status:
                if request.overwrite:
                    action, reason = (
                        OntologySyncAction.update,
                        f"status differs (local {local_status.value}, registry {status.value})",
                    )
                else:
                    action, reason = (
                        OntologySyncAction.skip,
                        f"status differs (local {local_status.value}, registry {status.value}); set overwrite=true",
                    )
            else:
                action, reason = OntologySyncAction.current, "already up to date"

            items.append(
                OntologySyncItem(
                    id=item_id,
                    ontology_family=family,
                    country=country,
                    version=version,
                    status=status,
                    issued_at=issued_at,
                    kind=kind,
                    visibility=visibility,
                    title=title,
                    ttl_url=ttl_url,
                    remote_sha256=remote_sha,
                    remote_byte_size=version_entry.get("byte_size"),
                    is_latest=is_latest,
                    is_stable=bool(version_entry.get("is_stable")),
                    is_default=bool(version_entry.get("is_default")),
                    local_exists=local is not None,
                    local_sha256=local_sha,
                    local_status=local_status,
                    action=action,
                    reason=reason,
                )
            )
    logger.info("Ontology sync plan built from %s: %d items", source_url, len(items))
    return items


def _apply_item(repo: OntologyRepository, item: OntologySyncItem, fetcher: Fetcher) -> OntologySyncItem:
    try:
        raw = fetcher(item.ttl_url)
    except UnsafeUrlError as exc:
        return item.model_copy(update={"action": OntologySyncAction.failed, "error": f"download refused: {exc}"})
    except httpx.HTTPError as exc:
        return item.model_copy(update={"action": OntologySyncAction.failed, "error": f"download failed: {exc}"})
    ttl = raw.decode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if item.remote_sha256 and digest != item.remote_sha256:
        return item.model_copy(
            update={
                "action": OntologySyncAction.failed,
                "error": f"sha256 mismatch: registry says {item.remote_sha256}, downloaded {digest}",
            }
        )
    try:
        published = repo.publish_version(
            OntologyPublishRequest(
                ontology_family=item.ontology_family,
                country=item.country,
                version=item.version,
                status=item.status,
                issued_at=item.issued_at,
                ttl=ttl,
                resource_filename=None,
                default_version=item.is_default,
            )
        )
    except (ValueError, GraphStoreError) as exc:
        logger.exception("Ontology sync failed for %s", item.id)
        return item.model_copy(update={"action": OntologySyncAction.failed, "error": str(exc)})
    return item.model_copy(
        update={
            "published": True,
            "local_exists": True,
            "local_sha256": digest,
            "local_status": item.status,
            "reason": "updated" if published.get("overwritten") else "published",
        }
    )


def sync_from_registry(
    repo: OntologyRepository,
    request: OntologySyncRequest,
    *,
    fetcher: Fetcher | None = None,
) -> OntologySyncResponse:
    source_url = request.source_url or settings.ONTOLOGY_REGISTRY_URL
    if fetcher is None:
        # A user-supplied registry, and every ttl_url it lists, is fetched through the SSRF guard.
        fetcher = _default_fetcher if is_configured_registry(request.source_url) else _guarded_fetcher
    registry = fetch_registry(source_url, fetcher)
    fetched_at = datetime.now(UTC)
    plan = build_sync_plan(repo, registry, request, source_url=source_url)

    results: list[OntologySyncItem] = []
    for item in plan:
        if request.dry_run or item.action not in {OntologySyncAction.publish, OntologySyncAction.update}:
            results.append(item)
            continue
        results.append(_apply_item(repo, item, fetcher))

    return OntologySyncResponse(
        source_url=source_url,
        registry=registry.get("registry"),
        schema_version=registry.get("schema_version"),
        dry_run=request.dry_run,
        fetched_at=fetched_at,
        total=len(results),
        to_publish=sum(1 for i in results if i.action == OntologySyncAction.publish and not i.published),
        to_update=sum(1 for i in results if i.action == OntologySyncAction.update and not i.published),
        current=sum(1 for i in results if i.action == OntologySyncAction.current),
        skipped=sum(1 for i in results if i.action == OntologySyncAction.skip),
        published=sum(1 for i in results if i.published),
        failed=sum(1 for i in results if i.action == OntologySyncAction.failed),
        items=results,
    )


__all__ = [
    "OntologyRegistryError",
    "OntologySourceNotAllowedError",
    "ensure_source_allowed",
    "build_sync_plan",
    "fetch_registry",
    "sync_from_registry",
]
