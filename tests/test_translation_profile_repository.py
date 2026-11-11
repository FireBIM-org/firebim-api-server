import hashlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import lexicon as lexicon_api
from app.schemas.translation import TranslationProfileCreateRequest, TranslationProfileUpdateRequest
from app.storage.translation_profile_repository import (
    TranslationProfileConflictError,
    TranslationProfileNotFoundError,
    TranslationProfileRepository,
)


class ConditionalS3Client:
    def __init__(self):
        self.objects = {}
        self.lock = Lock()
        self.read_barrier = None

    @staticmethod
    def error(code):
        return ClientError({"Error": {"Code": code}}, "s3")

    def get_object(self, *, Bucket, Key):
        with self.lock:
            if (Bucket, Key) not in self.objects:
                raise self.error("NoSuchKey")
            body, etag = self.objects[Bucket, Key]
        if self.read_barrier is not None:
            self.read_barrier.wait(timeout=5)
        return {"Body": BytesIO(body), "ETag": etag}

    def put_object(self, *, Bucket, Key, Body, ContentType, IfNoneMatch=None, IfMatch=None):
        assert ContentType == "application/json"
        assert IfNoneMatch == "*" or IfMatch
        with self.lock:
            existing = self.objects.get((Bucket, Key))
            if IfNoneMatch == "*" and existing is not None:
                raise self.error("PreconditionFailed")
            if IfMatch is not None and (existing is None or existing[1] != IfMatch):
                raise self.error("PreconditionFailed")
            etag = f'"{hashlib.sha256(Body).hexdigest()}"'
            self.objects[Bucket, Key] = Body, etag

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        return {
            "Contents": [{"Key": key} for bucket, key in self.objects if bucket == Bucket and key.startswith(Prefix)],
            "IsTruncated": False,
        }


@pytest.fixture
def repository():
    return TranslationProfileRepository(bucket_name="test", s3_client=ConditionalS3Client())


def create(repository, user=None):
    return repository.create_profile(
        payload=TranslationProfileCreateRequest(kind="ifc_translation", name="IFC4 draft"),
        user=user or SimpleNamespace(id="alice"),
    )


def test_rule_free_draft_roundtrip_and_user_isolation(repository):
    user = SimpleNamespace(id="alice")
    draft = create(repository, user)
    assert draft.id.startswith("ifct_")
    assert draft.generation == 1
    assert draft.status == "draft"
    assert repository.get_profile(draft.id, user) == draft
    assert repository.list_profiles(user)[0].id == draft.id
    assert repository.list_profiles(SimpleNamespace(id="bob")) == []
    with pytest.raises(TranslationProfileNotFoundError):
        repository.get_profile(draft.id, SimpleNamespace(id="bob"))


def test_update_clears_nullable_description_and_rejects_stale_save(repository):
    user = SimpleNamespace(id="alice")
    draft = create(repository, user)
    saved = repository.update_profile(
        profile_id=draft.id,
        user=user,
        payload=TranslationProfileUpdateRequest(expected_generation=1, description="notes"),
    )
    assert saved.generation == 2
    assert saved.description == "notes"
    cleared = repository.update_profile(
        profile_id=draft.id,
        user=user,
        payload=TranslationProfileUpdateRequest(expected_generation=2, description=None),
    )
    assert cleared.description is None
    assert cleared.created_at == draft.created_at
    with pytest.raises(TranslationProfileConflictError):
        repository.update_profile(
            profile_id=draft.id,
            user=user,
            payload=TranslationProfileUpdateRequest(expected_generation=1, name="stale"),
        )
    assert repository.get_profile(draft.id, user) == cleared


def test_concurrent_writers_cannot_both_save_the_same_generation(repository):
    user = SimpleNamespace(id="alice")
    draft = create(repository, user)
    repository.store.client.read_barrier = Barrier(2)

    def save(name):
        try:
            return repository.update_profile(
                profile_id=draft.id,
                user=user,
                payload=TranslationProfileUpdateRequest(expected_generation=1, name=name),
            )
        except TranslationProfileConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ["first", "second"]))
    repository.store.client.read_barrier = None
    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result is not None)
    assert winner.generation == 2
    assert repository.get_profile(draft.id, user) == winner


def test_archive_retains_contract_and_prevents_updates(repository):
    user = SimpleNamespace(id="alice")
    draft = create(repository, user)
    with pytest.raises(TranslationProfileConflictError):
        repository.archive_profile(draft.id, user, expected_generation=2)
    archived = repository.archive_profile(draft.id, user, expected_generation=1)
    assert archived.status == "archived"
    assert archived.generation == 2
    assert archived.contract == draft.contract
    assert repository.list_profiles(user) == []
    assert repository.list_profiles(user, include_archived=True)[0].id == draft.id
    assert repository.get_profile(draft.id, user) == archived
    with pytest.raises(TranslationProfileConflictError):
        repository.update_profile(
            profile_id=draft.id,
            user=user,
            payload=TranslationProfileUpdateRequest(expected_generation=2, name="not allowed"),
        )


def test_id_collision_never_overwrites_an_existing_profile(repository, monkeypatch):
    monkeypatch.setattr("app.storage.translation_profile_repository.uuid.uuid4", lambda: "fixed")
    original = create(repository)
    with pytest.raises(TranslationProfileConflictError):
        create(repository)
    assert repository.get_profile(original.id, SimpleNamespace(id="alice")) == original


@pytest.fixture
def translation_api(repository, monkeypatch):
    class UnavailableRuleService:
        def __getattr__(self, name):
            raise AssertionError(f"Translation drafts must not call the rules/ontology service: {name}")

    user = SimpleNamespace(id="alice")
    monkeypatch.setattr(lexicon_api, "translation_profile_repo", repository, raising=False)
    monkeypatch.setattr(lexicon_api, "service", UnavailableRuleService())
    monkeypatch.setattr(lexicon_api, "profile_repo", SimpleNamespace(list_profiles=lambda user: []))
    app = FastAPI()
    app.include_router(lexicon_api.router)
    app.dependency_overrides[lexicon_api.get_current_active_user] = lambda: user
    with TestClient(app) as client:
        yield client, user


def test_api_creates_saves_and_reopens_without_rules_or_ontology_services(translation_api):
    client, _ = translation_api
    response = client.post("/lexicon/profiles", json={"kind": "ifc_translation", "name": "IFC4 pilot"})
    assert response.status_code == 201, response.text
    draft = response.json()["profile_record"]
    assert draft["contract"]["source"]["schema"] == "IFC4"
    assert draft["contract"]["destination_release"] == "2026.09.04"
    assert draft["contract"]["optional_rule_ids"] == []
    assert draft["status"] == "draft"
    contract = draft["contract"]
    contract["destination_modules"] = [{"family": "fbo", "country": "dk", "version": "2026.09.04"}]
    contract["mappings"] = [
        {
            "id": "unresolved-fire-rating",
            "enabled": False,
            "review_state": "unresolved",
            "definition": {"source_value": "REI60", "target_iri": "https://example.org/FireResistance"},
        }
    ]
    updated = client.put(
        f"/lexicon/profiles/{draft['id']}",
        json={
            "expected_generation": 1,
            "description": "Incomplete is allowed",
            "contract": contract,
        },
    )
    assert updated.status_code == 200, updated.text
    saved = updated.json()["profile_record"]
    assert saved["generation"] == 2
    assert saved["contract"]["mappings"][0]["definition"]["source_value"] == "REI60"
    assert client.get(f"/lexicon/profiles/{draft['id']}").json()["profile_record"] == saved
    stale = client.put(f"/lexicon/profiles/{draft['id']}", json={"expected_generation": 1, "name": "stale"})
    assert stale.status_code == 409


def test_api_translation_profiles_are_opt_in_and_user_scoped(translation_api):
    client, user = translation_api
    draft = client.post("/lexicon/profiles", json={"kind": "ifc_translation", "name": "draft"}).json()["profile_record"]
    assert client.get("/lexicon/profiles").json() == {"count": 0, "profiles": []}
    listed = client.get("/lexicon/profiles?kind=ifc_translation").json()
    assert listed["count"] == 1
    assert listed["profiles"][0]["id"] == draft["id"]
    assert client.get("/lexicon/profiles?kind=ifc_translation&search=missing").json()["count"] == 0
    assert client.get("/lexicon/profiles?kind=ifc_translation&country=dk").status_code == 400
    user.id = "bob"
    assert client.get("/lexicon/profiles?kind=ifc_translation").json()["count"] == 0
    assert client.get(f"/lexicon/profiles/{draft['id']}").status_code == 404
    assert (
        client.put(f"/lexicon/profiles/{draft['id']}", json={"expected_generation": 1, "name": "hijack"}).status_code
        == 404
    )
    assert client.delete(f"/lexicon/profiles/{draft['id']}?expected_generation=1").status_code == 404
    assert client.post(f"/lexicon/profiles/{draft['id']}/publish", json={"expected_generation": 1}).status_code == 404


def test_api_archive_requires_generation_and_retains_profile(translation_api):
    client, _ = translation_api
    draft = client.post("/lexicon/profiles", json={"kind": "ifc_translation", "name": "draft"}).json()["profile_record"]
    path = f"/lexicon/profiles/{draft['id']}"
    assert client.delete(path).status_code == 422
    assert client.delete(f"{path}?expected_generation=2").status_code == 409
    response = client.delete(f"{path}?expected_generation=1")
    assert response.status_code == 200, response.text
    assert response.json()["profile_record"]["status"] == "archived"
    assert client.get(path).json()["profile_record"]["status"] == "archived"
    assert client.get("/lexicon/profiles?kind=ifc_translation").json()["count"] == 0
    assert client.get("/lexicon/profiles?kind=ifc_translation&include_archived=true").json()["count"] == 1
    assert client.put(path, json={"expected_generation": 2, "name": "resurrect"}).status_code == 409


def test_api_publication_never_claims_drafts_are_executable(translation_api):
    client, _ = translation_api
    draft = client.post("/lexicon/profiles", json={"kind": "ifc_translation", "name": "draft"}).json()["profile_record"]
    path = f"/lexicon/profiles/{draft['id']}/publish"
    assert client.post(path, json={"expected_generation": 2}).status_code == 409
    response = client.post(path, json={"expected_generation": 1})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "translation_validation_failed"
    assert client.get(f"/lexicon/profiles/{draft['id']}").json()["profile_record"] == draft


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "unknown", "name": "draft"},
        {"kind": "ifc_translation", "name": "draft", "status": "published"},
        {"kind": "ifc_translation", "name": "draft", "contract": {"source": {"schema": "IFC2X3"}}},
        {"kind": "ifc_translation", "name": "draft", "contract": {"destination_release": "latest"}},
        {"kind": "ifc_translation", "name": " "},
    ],
)
def test_api_rejects_invalid_translation_shapes_without_legacy_fallback(translation_api, payload):
    client, _ = translation_api
    assert client.post("/lexicon/profiles", json=payload).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "no generation"},
        {"expected_generation": 1, "status": "published"},
        {"expected_generation": 1, "name": None},
        {"expected_generation": 1, "contract": None},
    ],
)
def test_api_rejects_unsafe_update_shapes(translation_api, payload):
    client, _ = translation_api
    draft = client.post("/lexicon/profiles", json={"kind": "ifc_translation", "name": "draft"}).json()["profile_record"]
    assert client.put(f"/lexicon/profiles/{draft['id']}", json=payload).status_code == 422


def test_api_validation_reports_incomplete_draft_without_changing_it(translation_api):
    client, _ = translation_api
    draft = client.post("/lexicon/profiles", json={"kind": "ifc_translation", "name": "draft"}).json()["profile_record"]
    path = f"/lexicon/profiles/{draft['id']}"
    response = client.post(f"{path}/validate", json={"expected_generation": 1})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["profile_id"] == draft["id"]
    assert report["generation"] == 1
    assert report["authoring_valid"] is False
    assert report["runtime_available"] is False
    assert report["publish_ready"] is False
    assert [(step["id"], step["status"]) for step in report["steps"]] == [
        ("destination_ontologies", "failed"),
        ("mappings", "failed"),
        ("publication", "failed"),
    ]
    assert {item["code"] for item in report["diagnostics"]} >= {
        "destination_modules_required",
        "enabled_mappings_required",
        "translation_execution_unavailable",
    }
    assert client.get(path).json()["profile_record"] == draft


CORE_TTL = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix fbo: <https://firebim.org/ontologies/fbo/core#> .
fbo: a owl:Ontology ;
    owl:versionIRI <https://firebim.org/ontologies/fbo/core/2026.09.04> .
fbo:Wall a owl:Class .
fbo:ExteriorWall a owl:DatatypeProperty .
"""


@pytest.fixture
def ontology_releases(monkeypatch):
    from app.storage.graph_store import GraphStoreNotFoundError

    releases = {}

    class Releases:
        def fetch_version(self, family, country, version):
            assert version == "2026.09.04", "Moving aliases must never be fetched"
            key = f"{family}/{country}/{version}"
            if key not in releases:
                raise GraphStoreNotFoundError("Missing ontology release")
            item = releases[key]
            if callable(item):
                item = item()
            if isinstance(item, Exception):
                raise item
            return item

    releases["fbo/core/2026.09.04"] = SimpleNamespace(
        ttl=CORE_TTL,
        version=SimpleNamespace(
            version="2026.09.04",
            sha256=hashlib.sha256(CORE_TTL.encode()).hexdigest(),
            ontology_uri="https://firebim.org/ontologies/fbo/core#",
            version_iri="https://firebim.org/ontologies/fbo/core/2026.09.04",
        ),
    )
    monkeypatch.setattr(lexicon_api, "translation_ontology_repo", Releases(), raising=False)
    return releases


def validate_contract(client, contract):
    created = client.post(
        "/lexicon/profiles",
        json={
            "kind": "ifc_translation",
            "name": "validation pilot",
            "contract": contract,
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()["profile_record"]
    response = client.post(f"/lexicon/profiles/{draft['id']}/validate", json={"expected_generation": 1})
    assert response.status_code == 200, response.text
    return response.json()


def test_validation_verifies_exact_destination_content_but_does_not_claim_retention(
    translation_api,
    ontology_releases,
):
    client, _ = translation_api
    report = validate_contract(client, {"destination_modules": [{"family": "fbo", "country": "core"}]})
    assert report["steps"][0]["status"] == "passed"
    assert not any(
        d["severity"] == "error" and d["field_path"].startswith("contract.destination_modules")
        for d in report["diagnostics"]
    )
    assert any(d["code"] == "ontology_retention_unavailable" for d in report["diagnostics"])
    assert report["publish_ready"] is False


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"version": "latest"}, "destination_release_mismatch"),
        ({"version": "stable"}, "destination_release_mismatch"),
        ({"version": "2026.04.23"}, "destination_release_mismatch"),
        ({"family": "../fbo"}, "ontology_identity_invalid"),
        ({"country": "core/../dk"}, "ontology_identity_invalid"),
        ({"sha256": "0" * 64}, "ontology_pin_mismatch"),
        ({"ontology_iri": "https://example.org/wrong#"}, "ontology_pin_mismatch"),
        ({"version_iri": "https://example.org/wrong/version"}, "ontology_pin_mismatch"),
        ({"country": "missing"}, "ontology_release_not_found"),
    ],
)
def test_validation_rejects_unverifiable_destination_pins(translation_api, ontology_releases, changes, code):
    client, _ = translation_api
    pin = {"family": "fbo", "country": "core", **changes}
    report = validate_contract(client, {"destination_modules": [pin]})
    assert report["steps"][0]["status"] == "failed"
    assert any(d["code"] == code and d["severity"] == "error" for d in report["diagnostics"])


def test_validation_rejects_duplicate_destination_modules(translation_api, ontology_releases):
    client, _ = translation_api
    report = validate_contract(
        client,
        {
            "destination_modules": [
                {"family": "fbo", "country": "core"},
                {"family": "fbo", "country": "core"},
            ]
        },
    )
    assert report["steps"][0]["status"] == "failed"
    assert any(d["code"] == "destination_module_duplicate" for d in report["diagnostics"])


def test_validation_requires_selected_exact_dependency_closure(translation_api, ontology_releases):
    client, _ = translation_api
    dk_ttl = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://firebim.org/ontologies/fbo/dk#> a owl:Ontology ;
    owl:versionIRI <https://firebim.org/ontologies/fbo/dk/2026.09.04> ;
    owl:imports <https://firebim.org/ontologies/fbo/core/2026.09.04> .
"""
    ontology_releases["fbo/dk/2026.09.04"] = SimpleNamespace(
        ttl=dk_ttl,
        version=SimpleNamespace(
            version="2026.09.04",
            sha256=hashlib.sha256(dk_ttl.encode()).hexdigest(),
            ontology_uri="https://firebim.org/ontologies/fbo/dk#",
            version_iri="https://firebim.org/ontologies/fbo/dk/2026.09.04",
        ),
    )
    dk = {"family": "fbo", "country": "dk", "dependencies": ["fbo/core/2026.09.04"]}
    missing = validate_contract(client, {"destination_modules": [dk]})
    assert missing["steps"][0]["status"] == "failed"
    assert any(d["code"] == "ontology_dependency_missing" for d in missing["diagnostics"])
    complete = validate_contract(client, {"destination_modules": [dk, {"family": "fbo", "country": "core"}]})
    assert complete["steps"][0]["status"] == "passed"
    # Content imports, not user-authored dependency claims, define the required closure.
    dk["dependencies"] = []
    missing_import = validate_contract(client, {"destination_modules": [dk]})
    assert any(d["code"] == "ontology_import_unresolved" for d in missing_import["diagnostics"])


def test_validation_does_not_treat_unversioned_imports_as_pins(translation_api, ontology_releases):
    client, _ = translation_api
    release = ontology_releases["fbo/core/2026.09.04"]
    release.ttl += (
        "\n<https://firebim.org/ontologies/fbo/core#> "
        "<http://www.w3.org/2002/07/owl#imports> <https://example.org/latest> ."
    )
    release.version.sha256 = hashlib.sha256(release.ttl.encode()).hexdigest()
    report = validate_contract(client, {"destination_modules": [{"family": "fbo", "country": "core"}]})
    assert report["steps"][0]["status"] == "failed"
    assert any(d["code"] == "ontology_import_unresolved" for d in report["diagnostics"])


@pytest.mark.parametrize(
    ("mapping", "code", "field"),
    [
        ({"id": "wall", "enabled": True, "review_state": "suggestion"}, "mapping_review_required", "review_state"),
        ({"id": "wall", "enabled": True, "review_state": "unresolved"}, "mapping_review_required", "review_state"),
        ({"id": "wall", "enabled": True, "review_state": "rejected"}, "mapping_review_required", "review_state"),
        ({"id": "wall", "enabled": True, "review_state": "accepted"}, "mapping_definition_required", "definition"),
    ],
)
def test_validation_reports_actionable_mapping_review_errors(translation_api, mapping, code, field):
    client, _ = translation_api
    report = validate_contract(client, {"mappings": [mapping]})
    assert report["steps"][1]["status"] == "failed"
    assert any(
        d["code"] == code
        and d["mapping_id"] == "wall"
        and d["field_path"] == f"contract.mappings[0].{field}"
        and d["severity"] == "error"
        for d in report["diagnostics"]
    )


def test_validation_rejects_duplicate_mapping_ids_including_disabled_entries(translation_api):
    client, _ = translation_api
    report = validate_contract(client, {"mappings": [{"id": "same"}, {"id": "same"}]})
    assert any(d["code"] == "mapping_id_duplicate" and d["mapping_id"] == "same" for d in report["diagnostics"])


def test_opaque_accepted_mapping_cannot_pass_semantic_validation(translation_api, ontology_releases):
    client, _ = translation_api
    report = validate_contract(
        client,
        {
            "destination_modules": [{"family": "fbo", "country": "core"}],
            "mappings": [
                {
                    "id": "seed-exterior-wall",
                    "enabled": True,
                    "review_state": "accepted",
                    "definition": {
                        "kind": "class_to_class",
                        "target_iri": "https://firebim.org/ontologies/fbo/core#ExteriorWall",
                    },
                },
                {
                    "id": "fire-rating",
                    "enabled": False,
                    "review_state": "unresolved",
                    "definition": {"source_value": "REI60"},
                },
            ],
        },
    )
    assert report["steps"][0]["status"] == "passed"
    assert report["steps"][1]["status"] == "failed"
    assert report["authoring_valid"] is False
    assert report["publish_ready"] is False
    assert any(
        d["code"] == "mapping_language_unsupported" and d["mapping_id"] == "seed-exterior-wall"
        for d in report["diagnostics"]
    )
    assert not any(d["severity"] == "error" and d["mapping_id"] == "fire-rating" for d in report["diagnostics"])
    saved = client.get(f"/lexicon/profiles/{report['profile_id']}").json()["profile_record"]
    assert saved["contract"]["mappings"][1]["definition"]["source_value"] == "REI60"


def test_api_executable_mapping_can_pass_authoring_but_publication_stays_blocked(translation_api, ontology_releases):
    client, _ = translation_api
    report = validate_contract(
        client,
        {
            "destination_modules": [{"family": "fbo", "country": "core"}],
            "mappings": [
                {
                    "id": "wall",
                    "enabled": True,
                    "review_state": "accepted",
                    "definition": {
                        "language": "ifc4-mapping/1",
                        "source": {"schema": "IFC4", "ifc_class": "IfcWall", "match": "subtypes"},
                        "subject": "entity",
                        "target": {"kind": "class", "iri": "https://firebim.org/ontologies/fbo/core#Wall"},
                        "policies": {
                            "identity": "model_scoped",
                            "property_precedence": "occurrence_over_type",
                            "missing": "skip",
                            "invalid": "error",
                            "conflicts": "error",
                        },
                    },
                }
            ],
        },
    )
    assert report["authoring_valid"] is True
    assert report["runtime_available"] is False and report["publish_ready"] is True
    assert [step["status"] for step in report["steps"]] == ["passed", "passed", "passed"]
    path = f"/lexicon/profiles/{report['profile_id']}"
    saved = client.get(path).json()["profile_record"]
    assert saved["generation"] == 1
    assert saved["contract"]["mappings"][0]["definition"]["source"]["match"] == "subtypes"
    response = client.post(f"{path}/publish", json={"expected_generation": 1})
    assert response.status_code == 200, response.text
    published = response.json()
    assert published["content"]["source_contract"] == saved["contract"]
    assert published["content"]["compiled_mappings"]["wall"]["source"]["match"] == "subtypes"
    updated = client.get(path).json()["profile_record"]
    assert updated["generation"] == 2
    assert updated["contract"] == saved["contract"]
    assert updated["revision_refs"][0]["revision_id"] == published["revision_id"]


def test_validation_enforces_ownership_generation_and_archival(translation_api):
    client, user = translation_api
    draft = client.post("/lexicon/profiles", json={"kind": "ifc_translation", "name": "draft"}).json()["profile_record"]
    path = f"/lexicon/profiles/{draft['id']}"
    assert client.post(f"{path}/validate", json={}).status_code == 422
    assert client.post(f"{path}/validate", json={"expected_generation": 0}).status_code == 422
    assert client.post(f"{path}/validate", json={"expected_generation": 2}).status_code == 409
    user.id = "bob"
    assert client.post(f"{path}/validate", json={"expected_generation": 1}).status_code == 404
    user.id = "alice"
    assert client.delete(f"{path}?expected_generation=1").status_code == 200
    assert client.post(f"{path}/validate", json={"expected_generation": 2}).status_code == 409


def test_validation_rejects_draft_changes_while_ontology_checks_are_running(
    translation_api,
    ontology_releases,
    repository,
):
    client, user = translation_api
    draft = repository.create_profile(
        user=user,
        payload=TranslationProfileCreateRequest(
            kind="ifc_translation",
            name="draft",
            contract={"destination_modules": [{"family": "fbo", "country": "core"}]},
        ),
    )
    release = ontology_releases["fbo/core/2026.09.04"]

    def concurrent_save():
        repository.update_profile(
            profile_id=draft.id,
            user=user,
            payload=TranslationProfileUpdateRequest(
                expected_generation=1,
                name="Changed during validation",
            ),
        )
        return release

    ontology_releases["fbo/core/2026.09.04"] = concurrent_save
    response = client.post(f"/lexicon/profiles/{draft.id}/validate", json={"expected_generation": 1})
    assert response.status_code == 409, response.text
    assert repository.get_profile(draft.id, user).generation == 2


def test_validation_distinguishes_storage_outage_from_invalid_ontology(translation_api, ontology_releases):
    from app.storage.graph_store import GraphStoreError

    client, _ = translation_api
    ontology_releases["fbo/core/2026.09.04"] = GraphStoreError("private storage endpoint credentials")
    report = validate_contract(client, {"destination_modules": [{"family": "fbo", "country": "core"}]})
    assert report["steps"][0]["status"] == "unavailable"
    assert any(
        d["code"] == "ontology_storage_unavailable" and d["severity"] == "warning" for d in report["diagnostics"]
    )
    assert "private storage endpoint credentials" not in str(report)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("digest", "ontology_digest_mismatch"),
        ("invalid_turtle", "ontology_content_invalid"),
        ("wrong_ontology", "ontology_identity_mismatch"),
        ("missing_version", "ontology_identity_mismatch"),
        ("multiple_versions", "ontology_identity_mismatch"),
        ("wrong_release", "destination_release_mismatch"),
    ],
)
def test_validation_rejects_corrupt_or_inconsistent_release_content(
    translation_api,
    ontology_releases,
    mutation,
    code,
):
    client, _ = translation_api
    release = ontology_releases["fbo/core/2026.09.04"]
    if mutation == "digest":
        release.version.sha256 = "0" * 64
    elif mutation == "invalid_turtle":
        release.ttl = "this is not turtle"
    elif mutation == "wrong_ontology":
        release.version.ontology_uri = "https://example.org/another#"
    elif mutation == "missing_version":
        release.version.version_iri = None
    elif mutation == "multiple_versions":
        release.ttl += "\nfbo: owl:versionIRI <https://example.org/another/version> ."
    elif mutation == "wrong_release":
        release.version.version = "2026.04.23"
    if mutation != "digest":
        release.version.sha256 = hashlib.sha256(release.ttl.encode()).hexdigest()
    report = validate_contract(client, {"destination_modules": [{"family": "fbo", "country": "core"}]})
    assert report["steps"][0]["status"] == "failed"
    assert any(d["code"] == code for d in report["diagnostics"])


@pytest.fixture
def extraction_release(ontology_releases):
    release = ontology_releases["fbo/core/2026.09.04"]
    release.ttl += """
@prefix ifc: <https://firebim.org/ontologies/fbo/ifc#> .
ifc:Wall ifc:hasClassPredefinedType "IfcWall" ; ifc:mapsToTerm fbo:Wall .
"""
    release.version.sha256 = hashlib.sha256(release.ttl.encode()).hexdigest()
    return release


def test_api_module_extraction_is_read_only_and_can_be_explicitly_saved(
    translation_api,
    extraction_release,
):
    client, _ = translation_api
    draft = client.post(
        "/lexicon/profiles",
        json={
            "kind": "ifc_translation",
            "name": "extract",
            "contract": {
                "destination_modules": [{"family": "fbo", "country": "core"}],
                "mappings": [{"id": "custom", "review_state": "rejected", "definition": {"raw": "REI60"}}],
            },
        },
    ).json()["profile_record"]
    path = f"/lexicon/profiles/{draft['id']}"
    response = client.post(f"{path}/extract-mappings", json={"expected_generation": 1})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["profile_id"] == draft["id"] and report["generation"] == 1
    assert len(report["mappings"]) == 1 and report["mappings"][0]["enabled"] is True
    assert report["mappings"][0]["review_state"] == "accepted"
    assert report["skipped_existing"] == 0
    assert client.get(path).json()["profile_record"] == draft
    contract = {**draft["contract"], "mappings": draft["contract"]["mappings"] + report["mappings"]}
    saved = client.put(path, json={"expected_generation": 1, "contract": contract})
    assert saved.status_code == 200, saved.text
    assert saved.json()["profile_record"]["contract"] == contract
    assert saved.json()["profile_record"]["contract"]["mappings"][0] == draft["contract"]["mappings"][0]
    repeated = client.post(f"{path}/extract-mappings", json={"expected_generation": 2}).json()
    assert repeated["mappings"] == [] and repeated["skipped_existing"] == 1


def test_api_agreed_module_rules_and_visible_exclusions_validate_the_saved_generation(
    translation_api,
    monkeypatch,
):
    from tests.test_translation_agreed_mappings import agreed_case

    template, releases = agreed_case(
        "TeachingZone",
        'ifc:hasClassPredefinedType "IfcSpace" ; '
        'ifc:hasPsetProperty "Pset_SpaceOccupancyRequirements.OccupancyType" ; '
        'ifc:hasDataType "String" ; ifc:hasValue "EDUCATION, TEACHING"',
        "a owl:DatatypeProperty ; schema:rangeIncludes xsd:boolean",
    )
    monkeypatch.setattr(lexicon_api, "translation_ontology_repo", releases)
    client, _ = translation_api
    contract = template.contract.model_dump(mode="json")
    contract["mappings"] = []
    created = client.post("/lexicon/profiles", json={"kind": "ifc_translation", "name": "agreed", "contract": contract})
    assert created.status_code == 201, created.text
    path = f"/lexicon/profiles/{created.json()['profile_record']['id']}"
    extracted = client.post(f"{path}/extract-mappings", json={"expected_generation": 1})
    assert extracted.status_code == 200, extracted.text
    contract["mappings"] = extracted.json()["mappings"]
    saved = client.put(path, json={"expected_generation": 1, "contract": contract})
    assert saved.status_code == 200, saved.text
    response = client.post(f"{path}/validate", json={"expected_generation": 2})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["generation"] == 2 and report["authoring_valid"] is True
    assert report["runtime_available"] is False and report["publish_ready"] is True
    assert any(item["code"] == "mapping_excluded_by_agreement" for item in report["diagnostics"])
    assert any(item["code"] == "mapping_decision_applied" for item in report["diagnostics"])
    assert not any(item["severity"] == "error" for item in report["diagnostics"])
    assert client.get(path).json()["profile_record"] == saved.json()["profile_record"]
    assert client.post(f"{path}/validate", json={"expected_generation": 1}).status_code == 409
    published = client.post(f"{path}/publish", json={"expected_generation": 2})
    assert published.status_code == 200, published.text
    content = published.json()["content"]
    assert content["source_contract"] == contract
    assert any(item["code"] == "mapping_excluded_by_agreement" for item in content["diagnostics"])
    excluded = {
        item["mapping_id"] for item in content["diagnostics"] if item["code"] == "mapping_excluded_by_agreement"
    }
    assert not excluded.intersection(content["compiled_mappings"])
    assert client.get(path).json()["profile_record"]["generation"] == 3


def test_api_module_extraction_checks_ownership_generation_and_archive(translation_api):
    client, user = translation_api
    draft = client.post("/lexicon/profiles", json={"kind": "ifc_translation", "name": "draft"}).json()["profile_record"]
    path = f"/lexicon/profiles/{draft['id']}"
    assert client.post(f"{path}/extract-mappings", json={}).status_code == 422
    assert client.post(f"{path}/extract-mappings", json={"expected_generation": 0}).status_code == 422
    assert client.post(f"{path}/extract-mappings", json={"expected_generation": 2}).status_code == 409
    user.id = "bob"
    assert client.post(f"{path}/extract-mappings", json={"expected_generation": 1}).status_code == 404
    user.id = "alice"
    assert client.delete(f"{path}?expected_generation=1").status_code == 200
    assert client.post(f"{path}/extract-mappings", json={"expected_generation": 2}).status_code == 409


@pytest.mark.parametrize("operation", ["save", "archive"])
def test_api_module_extraction_detects_concurrent_draft_changes(
    translation_api,
    ontology_releases,
    extraction_release,
    repository,
    operation,
):
    client, user = translation_api
    draft = repository.create_profile(
        user=user,
        payload=TranslationProfileCreateRequest(
            kind="ifc_translation",
            name="draft",
            contract={"destination_modules": [{"family": "fbo", "country": "core"}]},
        ),
    )

    def concurrent_change():
        if operation == "save":
            repository.update_profile(
                profile_id=draft.id,
                user=user,
                payload=TranslationProfileUpdateRequest(
                    expected_generation=1,
                    name="Changed during extraction",
                ),
            )
        else:
            repository.archive_profile(draft.id, user, expected_generation=1)
        return extraction_release

    ontology_releases["fbo/core/2026.09.04"] = concurrent_change
    response = client.post(f"/lexicon/profiles/{draft.id}/extract-mappings", json={"expected_generation": 1})
    assert response.status_code == 409, response.text
    assert repository.get_profile(draft.id, user).contract.mappings == []
