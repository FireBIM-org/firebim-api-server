import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import lexicon as lexicon_api
from app.lexicon.ifc_mapping_evaluator import evaluate_ifc_mappings
from app.lexicon.translation_publication import prepare_translation_revision
from app.schemas.translation import (
    TranslationOntologyPin,
    TranslationProfileCreateRequest,
    TranslationProfileUpdateRequest,
)
from app.storage.graph_store import GraphStoreError
from app.storage.translation_profile_repository import (
    TranslationProfileConflictError,
    TranslationProfileNotFoundError,
    TranslationProfileRepository,
    TranslationProfileRepositoryError,
)
from tests.test_translation_executable_mappings import FBO, TTL, Releases, definition
from tests.test_translation_profile_repository import ConditionalS3Client


@pytest.fixture
def user():
    return SimpleNamespace(id="alice")


@pytest.fixture
def repository():
    return TranslationProfileRepository(bucket_name="test", s3_client=ConditionalS3Client())


def create_draft(repository, user):
    return repository.create_profile(
        user=user,
        payload=TranslationProfileCreateRequest(
            kind="ifc_translation",
            name="Published pilot",
            contract={
                "destination_modules": [{"family": "fbo", "country": "core"}],
                "mappings": [
                    {"id": "walls", "enabled": True, "review_state": "accepted", "definition": definition()},
                    {"id": "disabled", "enabled": False, "definition": {"unresolved": "REI60"}},
                ],
            },
        ),
    )


def test_preparation_freezes_verified_bytes_pins_compilation_and_disabled_definitions(repository, user):
    draft = create_draft(repository, user)
    before = copy.deepcopy(draft.model_dump(mode="json"))
    content = prepare_translation_revision(draft, Releases())
    assert content.profile_id == draft.id and content.draft_generation == 1
    assert content.source_contract.model_dump(mode="json") == before["contract"]
    assert list(content.compiled_mappings) == ["walls"]
    pin = content.contract.destination_modules[0]
    assert pin.ontology_iri == FBO
    assert pin.version_iri == "https://example.org/fbo/2026.09.04#"
    assert pin.sha256 == hashlib.sha256(TTL.encode()).hexdigest()
    assert pin.artifact_ref == f"sha256:{pin.sha256}"
    assert content.ontology_artifacts[0].ttl == TTL
    assert content.ontology_artifacts[0].pin == pin
    assert content.contract.mappings[1].definition == {"unresolved": "REI60"}
    assert not any(d.code == "ontology_retention_unavailable" for d in content.diagnostics)
    assert content.compiler_version and content.source_schema_provenance
    assert draft.model_dump(mode="json") == before


def test_committed_revision_survives_edits_archive_and_changed_or_deleted_libraries(repository, user):
    draft = create_draft(repository, user)
    releases = Releases()
    content = prepare_translation_revision(draft, releases)
    revision = repository.commit_revision(content, user)
    original = revision.model_dump_json()
    assert repository.get_profile(draft.id, user).generation == 2
    assert repository.get_revision(draft.id, revision.revision_id, user) == revision
    refs = repository.list_revisions(draft.id, user)
    assert len(refs) == 1 and refs[0].draft_generation == 1
    assert refs[0].content_digest == revision.content_digest
    assert repository.commit_revision(content, user) == revision
    assert repository.get_profile(draft.id, user).generation == 2

    repository.update_profile(
        profile_id=draft.id,
        user=user,
        payload=TranslationProfileUpdateRequest(
            expected_generation=2,
            name="Changed",
            contract={"mappings": []},
        ),
    )
    releases.ttl = "deleted"
    repository.archive_profile(draft.id, user, expected_generation=3)
    revision.content.contract.mappings.clear()
    assert repository.get_revision(draft.id, revision.revision_id, user).model_dump_json() == original
    assert repository.commit_revision(content, user).model_dump_json() == original
    assert len(repository.list_revisions(draft.id, user)) == 1


def test_concurrent_publications_of_one_generation_are_idempotent(repository, user):
    draft = create_draft(repository, user)
    content = prepare_translation_revision(draft, Releases())
    client = repository.store.client
    original = client.put_object
    barrier = Barrier(2)

    def put(**kwargs):
        if kwargs["Key"].startswith(repository.REVISION_PREFIX):
            barrier.wait(timeout=5)
        return original(**kwargs)

    client.put_object = put
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: repository.commit_revision(content, user), range(2)))
    assert results[0] == results[1]
    assert len(repository.list_revisions(draft.id, user)) == 1
    assert repository.get_profile(draft.id, user).generation == 2
    assert len([key for _, key in client.objects if key.startswith(repository.REVISION_PREFIX)]) == 1


@pytest.mark.parametrize("action", ["save", "archive", "storage_failure"])
def test_interrupted_publication_never_exposes_staged_snapshots(repository, user, action):
    draft = create_draft(repository, user)
    content = prepare_translation_revision(draft, Releases())
    client = repository.store.client
    original = client.put_object

    def put(**kwargs):
        result = original(**kwargs)
        if kwargs["Key"].startswith(repository.REVISION_PREFIX):
            client.put_object = original
            if action == "save":
                repository.update_profile(
                    profile_id=draft.id,
                    user=user,
                    payload=TranslationProfileUpdateRequest(
                        expected_generation=1,
                        name="A concurrent edit",
                    ),
                )
            elif action == "archive":
                repository.archive_profile(draft.id, user, expected_generation=1)
            else:
                raise client.error("ServiceUnavailable")
        return result

    client.put_object = put
    with pytest.raises(
        TranslationProfileRepositoryError if action == "storage_failure" else TranslationProfileConflictError
    ):
        repository.commit_revision(content, user)
    assert repository.list_revisions(draft.id, user) == []
    with pytest.raises(TranslationProfileNotFoundError):
        repository.get_revision(draft.id, f"ifctr_{content.digest()}", user)
    if action == "storage_failure":
        revision = repository.commit_revision(content, user)
        assert repository.get_revision(draft.id, revision.revision_id, user) == revision


def test_same_generation_with_different_content_is_rejected_and_cannot_cross_profiles_or_users(repository, user):
    draft = create_draft(repository, user)
    content = prepare_translation_revision(draft, Releases())
    revision = repository.commit_revision(content, user)
    changed = content.model_copy(deep=True)
    changed.compiled_mappings.clear()
    with pytest.raises(TranslationProfileConflictError):
        repository.commit_revision(changed, user)
    for read in (
        lambda: repository.get_revision(draft.id, revision.revision_id, SimpleNamespace(id="bob")),
        lambda: repository.list_revisions(draft.id, SimpleNamespace(id="bob")),
        lambda: repository.get_revision(create_draft(repository, user).id, revision.revision_id, user),
    ):
        with pytest.raises(TranslationProfileNotFoundError):
            read()


@pytest.fixture
def api(repository, user, monkeypatch):
    monkeypatch.setattr(lexicon_api, "translation_profile_repo", repository)
    monkeypatch.setattr(lexicon_api, "translation_ontology_repo", Releases())
    app = FastAPI()
    app.include_router(lexicon_api.router)
    app.dependency_overrides[lexicon_api.get_current_active_user] = lambda: user
    with TestClient(app) as client:
        yield client


def test_revision_read_api_is_owner_scoped_read_only_and_available_after_archive(api, repository, user):
    draft = create_draft(repository, user)
    path = f"/lexicon/profiles/{draft.id}/revisions"
    assert api.get(path).json() == {"count": 0, "revisions": []}
    revision = repository.commit_revision(prepare_translation_revision(draft, Releases()), user)
    repository.archive_profile(draft.id, user, expected_generation=2)
    listing = api.get(path)
    assert listing.status_code == 200, listing.text
    assert listing.json()["count"] == 1
    assert listing.json()["revisions"][0]["revision_id"] == revision.revision_id
    assert api.get(path + "?limit=1&offset=1").json() == {"count": 1, "revisions": []}
    assert api.get(path + "?limit=0").status_code == 422
    assert api.get(f"{path}/{revision.revision_id}").json() == revision.model_dump(mode="json")
    for method in (api.put, api.delete):
        assert method(f"{path}/{revision.revision_id}").status_code == 405
    assert api.get(f"{path}/unknown").status_code == 404
    user.id = "bob"
    assert api.get(path).status_code == 404
    assert api.get(f"{path}/{revision.revision_id}").status_code == 404


def test_validated_snapshot_is_publish_ready_without_upload_execution(api, repository, user):
    draft = create_draft(repository, user)
    path = f"/lexicon/profiles/{draft.id}"
    report = api.post(path + "/validate", json={"expected_generation": 1}).json()
    assert report["authoring_valid"] is True
    assert report["publish_ready"] is True
    assert report["runtime_available"] is False
    assert report["steps"][2]["status"] == "passed"
    assert any(
        item["code"] == "translation_execution_unavailable" and item["severity"] == "info"
        for item in report["diagnostics"]
    )
    assert repository.get_profile(draft.id, user) == draft
    assert repository.list_revisions(draft.id, user) == []


def test_snapshot_publication_requires_fresh_validation_not_an_upload_runtime(api, repository, user):
    draft = create_draft(repository, user)
    path = f"/lexicon/profiles/{draft.id}"
    response = api.post(path + "/publish", json={"expected_generation": 1})
    assert response.status_code == 200, response.text
    revision = response.json()
    assert revision["content"]["profile_id"] == draft.id
    assert revision["content"]["draft_generation"] == 1
    assert list(revision["content"]["compiled_mappings"]) == ["walls"]
    assert revision["content"]["ontology_artifacts"][0]["ttl"] == TTL
    assert revision["content"]["contract"]["mappings"][1]["enabled"] is False
    saved = api.get(path).json()["profile_record"]
    assert saved["generation"] == 2
    assert saved["status"] == "draft"
    assert saved["revision_refs"][0]["revision_id"] == revision["revision_id"]
    assert api.post(path + "/publish", json={"expected_generation": 1}).json() == revision
    assert repository.get_profile(draft.id, user).generation == 2


def test_successful_validation_cannot_bypass_changed_ontology_on_publication(api, repository, user, monkeypatch):
    draft = create_draft(repository, user)
    path = f"/lexicon/profiles/{draft.id}"
    checked = api.post(path + "/validate", json={"expected_generation": 1})
    assert checked.status_code == 200 and checked.json()["publish_ready"] is True
    monkeypatch.setattr(
        lexicon_api,
        "translation_ontology_repo",
        Releases(
            TTL.replace("f:Wall a owl:Class", "f:Other a owl:Class"),
        ),
    )
    rejected = api.post(path + "/publish", json={"expected_generation": 1})
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "translation_validation_failed"
    assert any(item["code"] == "mapping_target_not_declared" for item in rejected.json()["detail"]["diagnostics"])
    assert repository.list_revisions(draft.id, user) == []
    assert repository.get_profile(draft.id, user) == draft


def test_snapshot_publication_is_owner_scoped_before_any_content_is_staged(api, repository, user):
    draft = create_draft(repository, user)
    user.id = "bob"
    response = api.post(f"/lexicon/profiles/{draft.id}/publish", json={"expected_generation": 1})
    assert response.status_code == 404
    user.id = "alice"
    assert repository.list_revisions(draft.id, user) == []
    assert not any(key.startswith(repository.REVISION_PREFIX) for _, key in repository.store.client.objects)


def test_partial_ontology_storage_outage_cannot_publish_an_incomplete_snapshot(api, repository, user, monkeypatch):
    draft = create_draft(repository, user)
    draft.contract.destination_modules.append(TranslationOntologyPin(family="fbo", country="dk"))
    repository.update_profile(
        profile_id=draft.id,
        user=user,
        payload=TranslationProfileUpdateRequest(
            expected_generation=1,
            contract=draft.contract,
        ),
    )
    fetch = lexicon_api.translation_ontology_repo.fetch_version

    def partial_outage(family, country, version):
        if country == "dk":
            raise GraphStoreError("Private storage credentials")
        return fetch(family, country, version)

    monkeypatch.setattr(lexicon_api.translation_ontology_repo, "fetch_version", partial_outage)
    path = f"/lexicon/profiles/{draft.id}"
    checked = api.post(path + "/validate", json={"expected_generation": 2})
    assert checked.status_code == 200
    assert checked.json()["publish_ready"] is False
    assert checked.json()["steps"][2]["status"] == "unavailable"
    rejected = api.post(path + "/publish", json={"expected_generation": 2})
    assert rejected.status_code == 502, rejected.text
    assert rejected.json()["detail"]["code"] == "translation_validation_unavailable"
    assert "Private storage credentials" not in rejected.text
    assert repository.list_revisions(draft.id, user) == []
    assert repository.get_profile(draft.id, user).generation == 2
    assert not any(key.startswith(repository.REVISION_PREFIX) for _, key in repository.store.client.objects)


def test_publish_api_revalidates_freezes_and_replays_exact_generation(api, repository, user, monkeypatch):
    draft = create_draft(repository, user)
    path = f"/lexicon/profiles/{draft.id}/publish"
    response = api.post(path, json={"expected_generation": 1})
    assert response.status_code == 200, response.text
    revision = response.json()
    assert revision["content"]["compiled_mappings"]["walls"]["target"]["iri"] == FBO + "Wall"
    assert api.get(f"/lexicon/profiles/{draft.id}").json()["profile_record"]["generation"] == 2
    # A retry must not depend on a still-existing ontology library.
    monkeypatch.setattr(lexicon_api, "translation_ontology_repo", None)
    repository.archive_profile(draft.id, user, expected_generation=2)
    assert api.post(path, json={"expected_generation": 1}).json() == revision
    assert api.post(path, json={"expected_generation": 2}).status_code == 409


def test_publish_is_blocked_without_production_runtime_and_accepts_no_client_bypass(api, repository, user):
    draft = create_draft(repository, user)
    draft.contract.mappings.clear()
    draft = repository.update_profile(
        profile_id=draft.id,
        user=user,
        payload=TranslationProfileUpdateRequest(
            expected_generation=1,
            contract=draft.contract,
        ),
    )
    path = f"/lexicon/profiles/{draft.id}/publish"
    response = api.post(path, json={"expected_generation": 2})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "translation_validation_failed"
    assert any(item["code"] == "enabled_mappings_required" for item in response.json()["detail"]["diagnostics"])
    for claim in ("runtime_available", "authoring_valid", "publish_ready"):
        assert api.post(path, json={"expected_generation": 2, claim: True}).status_code == 422
    assert repository.get_profile(draft.id, user) == draft
    assert repository.list_revisions(draft.id, user) == []
    assert not any(key.startswith(repository.REVISION_PREFIX) for _, key in repository.store.client.objects)


@pytest.mark.parametrize(
    "failure", ["invalid_mapping", "invalid_ontology", "storage", "archive_during_check", "stale", "edit_during_check"]
)
def test_publish_rechecks_content_and_capabilities_without_creating_a_revision(
    api, repository, user, monkeypatch, failure
):
    draft = create_draft(repository, user)
    generation = 1
    if failure == "invalid_mapping":
        draft.contract.mappings[0].definition["target"]["iri"] = FBO + "NotDeclared"
        repository.update_profile(
            profile_id=draft.id,
            user=user,
            payload=TranslationProfileUpdateRequest(
                expected_generation=1,
                contract=draft.contract,
            ),
        )
        generation = 2
    elif failure == "invalid_ontology":
        monkeypatch.setattr(
            lexicon_api, "translation_ontology_repo", Releases(TTL.replace("f:Wall a owl:Class", "f:Other a owl:Class"))
        )
    elif failure == "storage":

        def unavailable(*args):
            raise GraphStoreError("Unavailable")

        monkeypatch.setattr(lexicon_api.translation_ontology_repo, "fetch_version", unavailable)
    elif failure == "stale":
        generation = 99
    else:
        fetch = lexicon_api.translation_ontology_repo.fetch_version

        def update_during_check(*args):
            if failure == "archive_during_check":
                repository.archive_profile(draft.id, user, expected_generation=1)
            else:
                repository.update_profile(
                    profile_id=draft.id,
                    user=user,
                    payload=TranslationProfileUpdateRequest(
                        expected_generation=1,
                        name="Edited concurrently",
                    ),
                )
            return fetch(*args)

        monkeypatch.setattr(lexicon_api.translation_ontology_repo, "fetch_version", update_during_check)
    response = api.post(f"/lexicon/profiles/{draft.id}/publish", json={"expected_generation": generation})
    expected = 422 if failure in {"invalid_mapping", "invalid_ontology"} else 502 if failure == "storage" else 409
    assert response.status_code == expected, response.text
    assert repository.list_revisions(draft.id, user) == []


def test_dependency_closure_and_exact_artifacts_are_frozen_from_one_fetch(repository, user):
    draft = create_draft(repository, user)
    draft.contract.destination_modules.append(TranslationOntologyPin(family="fbo", country="dk"))
    dk = """@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://example.org/dk#> a owl:Ontology ;
    owl:versionIRI <https://example.org/dk/2026.09.04#> ; owl:imports <https://example.org/fbo#> .
"""
    calls = []

    class Dependencies:
        def fetch_version(self, family, country, version):
            calls.append((family, country, version))
            if country == "core":
                assert calls.count((family, country, version)) == 1
                return Releases().fetch_version(family, country, version)
            return SimpleNamespace(
                ttl=dk,
                version=SimpleNamespace(
                    version=version,
                    ontology_uri="https://example.org/dk#",
                    version_iri="https://example.org/dk/2026.09.04#",
                    sha256=hashlib.sha256(dk.encode()).hexdigest(),
                ),
            )

    content = prepare_translation_revision(draft, Dependencies())
    assert len(calls) == 2
    assert content.contract.destination_modules[1].dependencies == ["fbo/core/2026.09.04"]
    assert [artifact.ttl for artifact in content.ontology_artifacts] == [TTL, dk]


def test_revision_digest_is_order_independent_and_read_detects_tampering(api, repository, user):
    draft = create_draft(repository, user)
    content = prepare_translation_revision(draft, Releases())
    encoded = content.model_dump(mode="json")
    reordered = type(content).model_validate(dict(reversed(list(encoded.items()))))
    assert content.digest() == reordered.digest()
    revision = repository.commit_revision(content, user)
    # Published compiled mappings can be evaluated without fetching any mutable libraries.
    result = evaluate_ifc_mappings(
        revision.content.compiled_mappings, [{"id": "wall", "ifc_class": "IfcWall"}], "model"
    )
    assert len(result.assertions) == 1 and result.assertions[0].object == FBO + "Wall"
    client = repository.store.client
    key = next(key for key in client.objects if key[1].startswith(repository.REVISION_PREFIX))
    payload, etag = client.objects[key]
    damaged = json.loads(payload)
    damaged["content"]["name"] = "Tampered"
    client.objects[key] = (json.dumps(damaged).encode(), etag)
    response = api.get(f"/lexicon/profiles/{draft.id}/revisions/{revision.revision_id}")
    assert response.status_code == 502, response.text


def test_successive_revisions_are_append_only_and_stale_saves_cannot_replace_history(
    api, repository, user, monkeypatch
):
    draft = create_draft(repository, user)
    path = f"/lexicon/profiles/{draft.id}"
    first = api.post(path + "/publish", json={"expected_generation": 1}).json()
    assert api.put(path, json={"expected_generation": 1, "name": "Stale"}).status_code == 409
    assert api.put(path, json={"expected_generation": 2, "revision_refs": []}).status_code == 422
    saved = api.put(path, json={"expected_generation": 2, "name": "Next draft"})
    assert saved.status_code == 200
    second = api.post(path + "/publish", json={"expected_generation": 3}).json()
    assert first["revision_id"] != second["revision_id"]
    assert first["content"]["name"] == "Published pilot"
    assert second["content"]["name"] == "Next draft"
    assert api.get(path + "/revisions").json()["revisions"] == [
        {
            "revision_id": revision["revision_id"],
            "draft_generation": revision["content"]["draft_generation"],
            "content_digest": revision["content_digest"],
            "published_at": revision["published_at"],
        }
        for revision in (second, first)
    ]
    assert api.get(path + "/revisions/" + first["revision_id"]).json() == first


def test_lost_commit_response_is_recoverable_without_duplicate_publication(api, repository, user, monkeypatch):
    draft = create_draft(repository, user)
    client = repository.store.client
    original = client.put_object

    def lose_commit_response(**kwargs):
        result = original(**kwargs)
        if kwargs["Key"].startswith(repository.ROOT_PREFIX):
            client.put_object = original
            raise client.error("ServiceUnavailable")
        return result

    client.put_object = lose_commit_response
    path = f"/lexicon/profiles/{draft.id}/publish"
    assert api.post(path, json={"expected_generation": 1}).status_code == 502
    retry = api.post(path, json={"expected_generation": 1})
    assert retry.status_code == 200, retry.text
    assert repository.get_profile(draft.id, user).generation == 2
    assert len(repository.list_revisions(draft.id, user)) == 1


def test_publication_committed_during_initial_read_is_replayed(api, repository, user, monkeypatch):
    draft = create_draft(repository, user)
    content = prepare_translation_revision(draft, Releases())
    client = repository.store.client
    original = client.get_object
    committed = []

    def commit_after_read(**kwargs):
        response = original(**kwargs)
        if kwargs["Key"].startswith(repository.ROOT_PREFIX):
            client.get_object = original
            committed.append(repository.commit_revision(content, user))
        return response

    client.get_object = commit_after_read
    result = api.post(f"/lexicon/profiles/{draft.id}/publish", json={"expected_generation": 1})
    assert result.status_code == 200, result.text
    assert result.json() == committed[0].model_dump(mode="json")
    assert len(repository.list_revisions(draft.id, user)) == 1
