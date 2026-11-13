import hashlib
import json
import os

# Minimal settings needed before importing the API modules.
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

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ontologies as ontologies_api
from app.ontologies.registry_sync import OntologyRegistryError, fetch_registry, sync_from_registry
from app.schemas.ontologies import OntologyReleaseStatus, OntologySyncAction, OntologySyncRequest
from app.storage.ontology_repository import OntologyManifestNotFoundError

TTL_NEW = (
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "<https://firebim.org/ontologies/fbo/dk#> a owl:Ontology ;\n"
    "    owl:versionIRI <https://firebim.org/ontologies/fbo/dk/2026.09.04#> .\n"
)
TTL_OLD = (
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "<https://firebim.org/ontologies/fbo/dk#> a owl:Ontology ;\n"
    "    owl:versionIRI <https://firebim.org/ontologies/fbo/dk/2026.04.23#> .\n"
)
TTL_IFC = (
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<https://firebim.org/ontologies/ifc/global#> a owl:Ontology .\n"
)

BASE = "https://firebim.org/documentation/ontologies"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def registry_payload() -> dict:
    return {
        "schema_version": 1,
        "registry": "FireBIM public ontology registry",
        "base_url": BASE,
        "ontologies": [
            {
                "ontology_family": "fbo",
                "country": "dk",
                "kind": "ontology",
                "visibility": "public",
                "title": "FBO-DK",
                "latest_version": "2026.09.04",
                "versions": [
                    {
                        "version": "2026.09.04",
                        "status": "draft",
                        "issued_at": "2026-09-04",
                        "ttl_url": f"{BASE}/fbo/dk/versions/2026.09.04/fbo-dk.ttl",
                        "sha256": sha(TTL_NEW),
                        "byte_size": len(TTL_NEW),
                        "is_latest": True,
                        "is_stable": False,
                        "is_default": True,
                    },
                    {
                        "version": "2026.04.23",
                        "status": "draft",
                        "issued_at": "2026-04-23",
                        "ttl_url": "fbo/dk/versions/2026.04.23/fbo-dk.ttl",  # relative to base_url
                        "sha256": sha(TTL_OLD),
                        "byte_size": len(TTL_OLD),
                        "is_latest": False,
                        "is_stable": False,
                        "is_default": False,
                    },
                ],
            },
            {
                "ontology_family": "ifc",
                "country": "global",
                "kind": "ontology",
                "visibility": "unlisted",
                "title": "IFC 4.3",
                "latest_version": "2026.04.23",
                "versions": [
                    {
                        "version": "2026.04.23",
                        "status": "candidate",
                        "issued_at": "2026-04-23",
                        "ttl_url": f"{BASE}/ifc/global/versions/2026.04.23/ifc-global.ttl",
                        "sha256": sha(TTL_IFC),
                        "byte_size": len(TTL_IFC),
                        "is_latest": True,
                        "is_stable": False,
                        "is_default": True,
                    }
                ],
            },
        ],
    }


FILES = {
    f"{BASE}/index.json": json.dumps(registry_payload()).encode(),
    f"{BASE}/fbo/dk/versions/2026.09.04/fbo-dk.ttl": TTL_NEW.encode(),
    f"{BASE}/fbo/dk/versions/2026.04.23/fbo-dk.ttl": TTL_OLD.encode(),
    f"{BASE}/ifc/global/versions/2026.04.23/ifc-global.ttl": TTL_IFC.encode(),
}


def fake_fetcher(url: str) -> bytes:
    if url not in FILES:
        raise httpx.HTTPError(f"404 {url}")
    return FILES[url]


class FakeRepo:
    """Only the manifest/publish surface the sync needs; 2026.04.23 already exists locally."""

    def __init__(self) -> None:
        self.manifests = {
            ("fbo", "dk"): {
                "ontology_family": "fbo",
                "country": "dk",
                "default_version": "2026.04.23",
                "versions": [
                    {
                        "version": "2026.04.23",
                        "status": "draft",
                        "sha256": sha(TTL_OLD),
                    }
                ],
            }
        }
        self.published: list = []

    def load_manifest(self, ontology_family: str, country: str) -> dict:
        try:
            return self.manifests[(ontology_family, country)]
        except KeyError as exc:
            raise OntologyManifestNotFoundError("missing") from exc

    def publish_version(self, payload) -> dict:
        self.published.append(payload)
        manifest = self.manifests.setdefault(
            (payload.ontology_family, payload.country),
            {"ontology_family": payload.ontology_family, "country": payload.country, "versions": []},
        )
        overwritten = any(v["version"] == payload.version for v in manifest["versions"])
        manifest["versions"] = [v for v in manifest["versions"] if v["version"] != payload.version]
        manifest["versions"].append(
            {"version": payload.version, "status": payload.status.value, "sha256": sha(payload.ttl)}
        )
        return {"published": True, "overwritten": overwritten}


def test_fetch_registry_rejects_bad_payload():
    with pytest.raises(OntologyRegistryError):
        fetch_registry("https://example.org/index.json", lambda _url: b"not json")
    with pytest.raises(OntologyRegistryError):
        fetch_registry("https://example.org/index.json", lambda _url: b'{"schema_version": 99, "ontologies": []}')
    with pytest.raises(OntologyRegistryError):
        fetch_registry("https://example.org/index.json", lambda _url: b'{"foo": 1}')


def test_dry_run_plans_without_publishing():
    repo = FakeRepo()
    response = sync_from_registry(
        repo,
        OntologySyncRequest(source_url=f"{BASE}/index.json", dry_run=True),
        fetcher=fake_fetcher,
    )
    assert response.dry_run is True
    assert repo.published == []
    actions = {item.id: item.action for item in response.items}
    assert actions == {
        "fbo/dk/2026.09.04": OntologySyncAction.publish,
        "fbo/dk/2026.04.23": OntologySyncAction.current,
        "ifc/global/2026.04.23": OntologySyncAction.publish,
    }
    assert response.to_publish == 2
    assert response.current == 1
    # newest first, as in the registry
    assert [item.version for item in response.items if item.ontology_family == "fbo"] == ["2026.09.04", "2026.04.23"]
    # relative ttl_url resolved against base_url
    old = next(item for item in response.items if item.version == "2026.04.23" and item.ontology_family == "fbo")
    assert old.ttl_url == f"{BASE}/fbo/dk/versions/2026.04.23/fbo-dk.ttl"


def test_sync_publishes_new_versions_and_verifies_sha():
    repo = FakeRepo()
    response = sync_from_registry(
        repo,
        OntologySyncRequest(source_url=f"{BASE}/index.json"),
        fetcher=fake_fetcher,
    )
    assert response.published == 2
    assert response.failed == 0
    assert {(p.ontology_family, p.country, p.version) for p in repo.published} == {
        ("fbo", "dk", "2026.09.04"),
        ("ifc", "global", "2026.04.23"),
    }
    new = next(p for p in repo.published if p.version == "2026.09.04")
    assert new.status == OntologyReleaseStatus.draft
    assert new.default_version is True
    assert new.ttl == TTL_NEW

    # second run: everything current
    again = sync_from_registry(repo, OntologySyncRequest(source_url=f"{BASE}/index.json"), fetcher=fake_fetcher)
    assert again.published == 0
    assert all(item.action == OntologySyncAction.current for item in again.items)


def test_sync_skips_unlisted_and_filters():
    repo = FakeRepo()
    response = sync_from_registry(
        repo,
        OntologySyncRequest(source_url=f"{BASE}/index.json", include_unlisted=False, latest_only=True),
        fetcher=fake_fetcher,
    )
    assert [item.id for item in response.items] == ["fbo/dk/2026.09.04"]

    repo = FakeRepo()
    response = sync_from_registry(
        repo,
        OntologySyncRequest(source_url=f"{BASE}/index.json", selections=["ifc/global/2026.04.23"]),
        fetcher=fake_fetcher,
    )
    assert [item.id for item in response.items] == ["ifc/global/2026.04.23"]
    assert response.published == 1


def test_sync_content_drift_requires_overwrite():
    repo = FakeRepo()
    repo.manifests[("fbo", "dk")]["versions"][0]["sha256"] = "stale"
    response = sync_from_registry(
        repo,
        OntologySyncRequest(source_url=f"{BASE}/index.json", selections=["fbo/dk/2026.04.23"]),
        fetcher=fake_fetcher,
    )
    item = response.items[0]
    assert item.action == OntologySyncAction.skip
    assert "overwrite" in item.reason

    response = sync_from_registry(
        repo,
        OntologySyncRequest(source_url=f"{BASE}/index.json", selections=["fbo/dk/2026.04.23"], overwrite=True),
        fetcher=fake_fetcher,
    )
    assert response.items[0].published is True
    assert response.items[0].reason == "updated"


def test_sync_reports_download_failure():
    repo = FakeRepo()
    payload = registry_payload()
    payload["ontologies"][0]["versions"][0]["ttl_url"] = f"{BASE}/missing.ttl"
    files = {f"{BASE}/index.json": json.dumps(payload).encode()}

    def fetcher(url: str) -> bytes:
        if url in files:
            return files[url]
        raise httpx.HTTPError("404")

    response = sync_from_registry(
        repo, OntologySyncRequest(source_url=f"{BASE}/index.json", selections=["fbo/dk/2026.09.04"]), fetcher=fetcher
    )
    assert response.failed == 1
    assert response.items[0].action == OntologySyncAction.failed
    assert "download failed" in (response.items[0].error or "")


def test_sync_endpoints(monkeypatch):
    repo = FakeRepo()
    monkeypatch.setattr(ontologies_api, "repo", repo)
    monkeypatch.setattr(
        ontologies_api,
        "sync_from_registry",
        lambda r, request: sync_from_registry(r, request, fetcher=fake_fetcher),
    )
    app = FastAPI()
    app.include_router(ontologies_api.router)
    app.dependency_overrides[ontologies_api.get_current_active_user] = lambda: object()
    client = TestClient(app)

    source = client.get("/ontologies/sync/source")
    assert source.status_code == 200
    assert source.json()["default_source_url"].startswith("https://firebim.org/documentation/ontologies")

    preview = client.post("/ontologies/sync/preview", json={"source_url": f"{BASE}/index.json"})
    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert repo.published == []

    result = client.post("/ontologies/sync", json={"source_url": f"{BASE}/index.json", "families": ["fbo"]})
    assert result.status_code == 200
    body = result.json()
    assert body["published"] == 1
    assert body["items"][0]["id"] == "fbo/dk/2026.09.04"

    bad = client.post("/ontologies/sync", json={"source_url": "ftp://nope"})
    assert bad.status_code == 422

    missing = client.post("/ontologies/sync/preview", json={"source_url": "https://example.org/none.json"})
    assert missing.status_code == 502


def test_async_sync_job_endpoints(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    request_payload = {"source_url": f"{BASE}/index.json", "selections": ["ifc/global/2026.04.23"]}
    record = {
        "job_id": "2a61b84d-3021-47bd-a5e2-bfe5ad9d052c",
        "owner_id": "user-1",
        "status": "queued",
        "created_at": datetime.now(UTC).isoformat(),
        "started_at": None,
        "completed_at": None,
        "request": request_payload,
        "result": None,
        "error": None,
    }
    fake_store = SimpleNamespace(
        create=lambda **_kwargs: record,
        get=lambda _job_id: record,
        update=lambda _job_id, **changes: {**record, **changes},
    )
    queued: list[tuple[str, list]] = []
    fake_celery = SimpleNamespace(send_task=lambda name, args: queued.append((name, args)))
    monkeypatch.setattr(ontologies_api, "sync_job_store", fake_store)
    monkeypatch.setattr(ontologies_api, "celery_app", fake_celery)

    app = FastAPI()
    app.include_router(ontologies_api.router)
    app.dependency_overrides[ontologies_api.get_current_active_user] = lambda: SimpleNamespace(id="user-1")
    client = TestClient(app)

    started = client.post("/ontologies/sync/jobs", json=request_payload)
    assert started.status_code == 202
    assert started.json()["status"] == "queued"
    assert queued[0][0] == "sync_ontologies_from_registry"

    fetched = client.get(f"/ontologies/sync/jobs/{record['job_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["job_id"] == record["job_id"]
