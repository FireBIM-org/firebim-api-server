import hashlib
from io import BytesIO

import pytest
from sqlmodel import select

from app.auth.jwt import get_current_active_user
from app.lexicon.translation_publication import prepare_translation_revision
from app.main import app
from app.storage.translation_profile_repository import TranslationProfileRepository
from tests.test_translation_executable_mappings import Releases
from tests.test_translation_profile_repository import ConditionalS3Client
from tests.test_translation_revisions import create_draft

IFC = b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n#1=IFCPROJECT($);\nENDSEC;\nEND-ISO-10303-21;"


@pytest.fixture
def admission_api(client, session, test_user, monkeypatch):
    from app.api.models import get_ingestion_admission
    from app.ingestion.admission import IngestionAdmission
    from app.ingestion.jobs import AdmissionRuntime
    from app.storage.ingestion_objects import IngestionObjects

    repository = TranslationProfileRepository(bucket_name="test", s3_client=ConditionalS3Client())
    draft = create_draft(repository, test_user)
    revision = repository.commit_revision(prepare_translation_revision(draft, Releases()), test_user)

    class Objects:
        def __init__(self):
            self.values = {}
            self.puts = []

        def generate_presigned_url(self, operation, *, Params, ExpiresIn, HttpMethod):
            assert operation == "put_object" and HttpMethod == "PUT"
            return "https://private-storage.test/" + Params["Key"]

        def get_object(self, *, Bucket, Key):
            if Key not in self.values:
                raise ConditionalS3Client.error("NoSuchKey")
            data = self.values[Key]
            return {"Body": BytesIO(data), "ContentLength": len(data)}

        def put_object(self, **kwargs):
            key = kwargs["Key"]
            self.puts.append(kwargs)
            if key in self.values:
                raise ConditionalS3Client.error("PreconditionFailed")
            assert kwargs["IfNoneMatch"] == "*"
            self.values[key] = kwargs["Body"]
            return {}

        def head_object(self, *, Bucket, Key):
            if Key not in self.values:
                raise ConditionalS3Client.error("NoSuchKey")
            return {"ContentLength": len(self.values[Key])}

    objects = Objects()
    service = IngestionAdmission(
        session,
        repository,
        IngestionObjects(objects, "test"),
        AdmissionRuntime(enabled=True, max_input_bytes=8192),
    )
    app.dependency_overrides[get_ingestion_admission] = lambda: service
    yield client, service, objects, revision
    app.dependency_overrides.pop(get_ingestion_admission, None)


def initiate(api, **changes):
    client, _, _, revision = api
    payload = {
        "request_id": "d29dd26e-b135-49f9-bb62-02e3c09843ab",
        "filename": "building.ifc",
        "size_bytes": len(IFC),
        "sha256": hashlib.sha256(IFC).hexdigest(),
        "profile_id": revision.content.profile_id,
        "revision_id": revision.revision_id,
    }
    return client.post("/ifc-models/uploads", json={**payload, **changes})


def complete(api):
    client, _, objects, _ = api
    upload = initiate(api)
    assert upload.status_code == 201, upload.text
    upload = upload.json()
    staging_key = upload["upload_url"].removeprefix("https://private-storage.test/")
    objects.values[staging_key] = IFC
    response = client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={})
    return upload, staging_key, response


def test_completed_upload_is_revision_bound_durable_and_not_broker_dependent(admission_api, session, mock_celery):
    from app.models.models import IfcModel, IngestionJob, IngestionOutbox

    mock_celery.send_task.side_effect = ConnectionError("broker unavailable")
    client, _, objects, revision = admission_api
    upload, staging_key, response = complete(admission_api)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "queued"
    assert job["revision_id"] == revision.revision_id
    assert job["revision_digest"] == revision.content_digest
    assert job["raw_sha256"] == hashlib.sha256(IFC).hexdigest()
    saved = session.get(IngestionJob, job["id"])
    assert saved.raw_key != staging_key
    assert objects.values[saved.raw_key] == IFC
    assert saved.revision_snapshot == revision.model_dump(mode="json")
    assert len(saved.runtime_fingerprint) == 64
    assert session.get(IfcModel, saved.model_id).ingestion_backend == "neo"
    assert session.exec(select(IngestionOutbox)).one().job_id == saved.id
    mock_celery.send_task.assert_not_called()

    objects.values[staging_key] = b"overwritten using the still-valid staging URL"
    replay = client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={})
    assert replay.status_code == 202 and replay.json()["id"] == job["id"]
    assert objects.values[saved.raw_key] == IFC
    assert len(session.exec(select(IngestionJob)).all()) == 1
    assert len(session.exec(select(IngestionOutbox)).all()) == 1
    assert client.get(f"/ifc-models/jobs/{saved.id}").json()["id"] == saved.id


def test_upload_intent_replay_is_payload_bound_and_does_not_reissue_completed_write_url(admission_api):
    client, _, _, _ = admission_api
    first = initiate(admission_api)
    assert first.status_code == 201
    replay = initiate(admission_api)
    assert replay.status_code == 201 and replay.json()["id"] == first.json()["id"]
    assert initiate(admission_api, filename="different.ifc").status_code == 409
    upload, _, completed = complete(admission_api)
    assert completed.status_code == 202
    status = client.get(f"/ifc-models/uploads/{upload['id']}")
    assert status.status_code == 200
    assert status.json()["upload_url"] is None
    assert initiate(admission_api).json()["job_id"] == completed.json()["id"]


@pytest.mark.parametrize("kind", ["hash", "size", "ifc2x3", "ifc4x3", "fake_schema", "truncated", "binary"])
def test_completion_verifies_actual_bytes_and_schema_before_admission(admission_api, session, kind):
    from app.models.models import IfcModel, IngestionJob, IngestionOutbox

    client, _, objects, _ = admission_api
    contents = {
        "hash": IFC.replace(b"IFCPROJECT", b"IFCBUILDNG"),
        "size": IFC + b"x",
        "ifc2x3": IFC.replace(b"'IFC4'", b"'IFC2X3'"),
        "ifc4x3": IFC.replace(b"'IFC4'", b"'IFC4X3'"),
        "fake_schema": IFC.replace(b"FILE_SCHEMA(('IFC4'));", b"/* FILE_SCHEMA(('IFC4')); */FILE_SCHEMA(('IFC2X3'));"),
        "truncated": IFC[:-1],
        "binary": IFC.replace(b"HEADER", b"\x00EADER"),
    }
    data = contents[kind]
    changes = {} if kind in {"hash", "size"} else {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    upload = initiate(admission_api, **changes).json()
    objects.values[upload["upload_url"].removeprefix("https://private-storage.test/")] = data
    response = client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={})
    assert response.status_code == 422, response.text
    assert not session.exec(select(IngestionJob)).all()
    assert not session.exec(select(IngestionOutbox)).all()
    assert not session.exec(select(IfcModel)).all()
    assert not objects.puts


def test_owner_checks_cover_sessions_jobs_reports_retry_reprocess_and_revision_discovery(admission_api, session):
    from app.models.models import User

    client, _, _, revision = admission_api
    upload, _, response = complete(admission_api)
    job = response.json()
    other = User(authentik_sub="bob", username="bob", email="bob@example.org")
    session.add(other)
    session.commit()
    app.dependency_overrides[get_current_active_user] = lambda: other
    for path in (f"uploads/{upload['id']}", f"jobs/{job['id']}", f"jobs/{job['id']}/report", f"{job['model_id']}/jobs"):
        assert client.get("/ifc-models/" + path).status_code == 404
    for path, payload in (
        (f"uploads/{upload['id']}/complete", {}),
        (f"jobs/{job['id']}/cancel", {}),
        (
            f"jobs/{job['id']}/retry",
            {
                "request_id": "87c2fa11-4ba8-46e3-a84a-c2f5d6344593",
                "expected_dispatch_generation": 1,
            },
        ),
        (
            f"{job['model_id']}/reprocess",
            {
                "request_id": "87c2fa11-4ba8-46e3-a84a-c2f5d6344593",
                "profile_id": revision.content.profile_id,
                "revision_id": revision.revision_id,
            },
        ),
    ):
        assert client.post("/ifc-models/" + path, json=payload).status_code == 404
    assert client.get("/ifc-models/ingestion-revisions").json()["revisions"] == []
    assert initiate(admission_api, request_id="87c2fa11-4ba8-46e3-a84a-c2f5d6344593").status_code == 404


def test_outbox_failure_rolls_back_model_job_and_upload_but_raw_snapshot_can_be_reused(
    admission_api,
    session,
    monkeypatch,
):
    from app.models.models import IfcModel, IfcUploadSession, IngestionJob, IngestionOutbox

    client, _, objects, _ = admission_api
    upload = initiate(admission_api).json()
    staging_key = upload["upload_url"].removeprefix("https://private-storage.test/")
    objects.values[staging_key] = IFC
    original = session.commit

    def fail_commit():
        if any(isinstance(item, IngestionOutbox) for item in session.new):
            raise RuntimeError("database write unavailable")
        return original()

    monkeypatch.setattr(session, "commit", fail_commit)
    assert client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={}).status_code == 500
    assert not session.exec(select(IfcModel)).all()
    assert not session.exec(select(IngestionJob)).all()
    assert not session.exec(select(IngestionOutbox)).all()
    assert session.get(IfcUploadSession, upload["id"]).status == "uploading"
    monkeypatch.setattr(session, "commit", original)
    assert client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={}).status_code == 202
    assert len(objects.puts) == 2


def test_write_once_final_input_cannot_be_replaced(admission_api, session):
    from app.models.models import IfcUploadSession, IngestionJob

    client, _, objects, _ = admission_api
    upload = initiate(admission_api).json()
    stored = session.get(IfcUploadSession, upload["id"])
    objects.values[stored.staging_key] = IFC
    raw_key = f"ingestion/raw/{stored.user_id}/{stored.id}/{stored.expected_sha256}.ifc"
    objects.values[raw_key] = b"immutable but wrong bytes"
    response = client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={})
    assert response.status_code == 422
    assert objects.values[raw_key] == b"immutable but wrong bytes"
    assert not session.exec(select(IngestionJob)).all()


def test_semantic_fixture_with_quoted_header_content_is_accepted(admission_api):
    from tests.test_neo_ifcowl_adapter import FIXTURES

    client, _, objects, _ = admission_api
    data = (FIXTURES / "semantic.ifc").read_bytes().replace(b"hand-authored", b"ENDSEC; FILE_SCHEMA((''IFC2X3''));")
    upload = initiate(admission_api, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()).json()
    objects.values[upload["upload_url"].removeprefix("https://private-storage.test/")] = data
    response = client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={})
    assert response.status_code == 202, response.text


@pytest.mark.parametrize("kind", ["tampered_snapshot", "catalogue", "ontology", "unlinked"])
def test_corrupt_and_runtime_unsupported_revisions_are_not_offered_or_admitted(admission_api, test_user, kind):
    import json

    client, service, _, revision = admission_api
    repository = service.profiles
    user = test_user
    if kind == "unlinked":
        assert initiate(admission_api, revision_id="ifctr_unlinked").status_code == 404
        return
    key = repository.store.document_key(repository.REVISION_PREFIX, user, revision.revision_id)
    objects = repository.store.client.objects
    body, etag = objects["test", key]
    if kind == "tampered_snapshot":
        record = json.loads(body)
        record["content"]["name"] = "changed without rehashing"
        objects["test", key] = json.dumps(record).encode(), etag
        expected = 503
    else:
        # Persistence does not attest to runtime support; admission must independently reject it.
        content = revision.content.model_copy(deep=True)
        content.draft_generation += 1
        if kind == "catalogue":
            content.source_schema_provenance["sha256"] = "different catalogue"
        else:
            content.ontology_artifacts[0].ttl += "\ncorrupted bytes"
        draft = repository.get_profile(revision.content.profile_id, user)
        content.source_contract = draft.contract
        saved = repository.commit_revision(content, user)
        revision = saved
        expected = 422
    response = initiate(admission_api, revision_id=revision.revision_id)
    assert response.status_code == expected, response.text
    offered = client.get("/ifc-models/ingestion-revisions")
    assert offered.status_code == 200
    assert revision.revision_id not in [item["revision_id"] for item in offered.json()["revisions"]]


def test_completion_revalidates_published_revision_instead_of_trusting_initiation(admission_api):
    import json

    client, service, objects, revision = admission_api
    upload = initiate(admission_api).json()
    objects.values[upload["upload_url"].removeprefix("https://private-storage.test/")] = IFC
    repository = service.profiles
    key = next(key for key in repository.store.client.objects if revision.revision_id in key[1])
    body, etag = repository.store.client.objects[key]
    record = json.loads(body)
    record["content"]["name"] = "tampered"
    repository.store.client.objects[key] = json.dumps(record).encode(), etag
    response = client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={})
    assert response.status_code == 503
    assert not objects.puts


def test_retry_is_bounded_and_idempotent_and_reprocessing_preserves_success(admission_api, session):
    from app.models.models import IfcModel, IngestionAttempt, IngestionJob, IngestionOutbox

    client, _, _, revision = admission_api
    _, _, response = complete(admission_api)
    job = response.json()
    retry = {"request_id": "87c2fa11-4ba8-46e3-a84a-c2f5d6344593", "expected_dispatch_generation": 1}
    assert client.post(f"/ifc-models/jobs/{job['id']}/retry", json=retry).status_code == 409
    stored = session.get(IngestionJob, job["id"])
    stored.status, stored.retryable, stored.attempt_count = "failed", True, 1
    session.add(stored)
    session.add(
        IngestionAttempt(
            job_id=stored.id,
            number=1,
            fencing_token=1,
            status="failed",
            stage="conversion",
            runtime_fingerprint=stored.runtime_fingerprint,
            diagnostics=[{"code": "producer_timeout"}],
        )
    )
    session.commit()
    report = client.get(f"/ifc-models/jobs/{job['id']}/report").json()
    assert report["report_version"] == "neo-ingestion/1"
    assert report["attempts"][0]["diagnostics"] == [{"code": "producer_timeout"}]
    assert "artifacts" not in report["attempts"][0]
    response = client.post(f"/ifc-models/jobs/{job['id']}/retry", json=retry)
    assert response.status_code == 202, response.text
    assert response.json()["id"] == job["id"] and response.json()["dispatch_generation"] == 2
    assert client.post(f"/ifc-models/jobs/{job['id']}/retry", json=retry).json()["dispatch_generation"] == 2
    assert len(session.exec(select(IngestionOutbox)).all()) == 2
    assert (
        client.post(
            f"/ifc-models/jobs/{job['id']}/retry",
            json={
                **retry,
                "expected_dispatch_generation": 2,
            },
        ).status_code
        == 409
    )
    stored.status, stored.retryable, stored.attempt_count = "failed", False, 3
    session.add(stored)
    session.commit()
    assert (
        client.post(
            f"/ifc-models/jobs/{job['id']}/retry",
            json={
                "request_id": "c20646c7-cdb9-4ff0-b04f-b02ee133dc19",
                "expected_dispatch_generation": 2,
            },
        ).status_code
        == 409
    )

    stored.status, stored.result_graph_id = "succeeded", "immutable-graph-1"
    model = session.get(IfcModel, stored.model_id)
    model.latest_successful_graph_id = stored.result_graph_id
    model.latest_successful_job_id, model.latest_successful_revision_id = stored.id, stored.revision_id
    session.add(stored)
    session.add(model)
    session.commit()
    reprocess = {
        "request_id": "c20646c7-cdb9-4ff0-b04f-b02ee133dc19",
        "profile_id": revision.content.profile_id,
        "revision_id": revision.revision_id,
    }
    response = client.post(f"/ifc-models/{model.id}/reprocess", json=reprocess)
    assert response.status_code == 202, response.text
    new_job = response.json()
    assert new_job["id"] != stored.id and new_job["raw_sha256"] == stored.raw_sha256
    assert client.post(f"/ifc-models/{model.id}/reprocess", json=reprocess).json()["id"] == new_job["id"]
    assert len(session.exec(select(IngestionJob)).all()) == 2
    detail = client.get(f"/ifc-models/{model.id}").json()
    assert detail["latest_successful_graph_id"] == "immutable-graph-1"
    assert detail["latest_job_id"] == new_job["id"]
    assert len(client.get(f"/ifc-models/{model.id}/jobs").json()) == 2


def test_neo_model_cannot_accept_sql_payload_and_delete_does_not_resurrect_jobs(admission_api, session, mock_s3):
    from app.models.models import IngestionJob

    client, _, objects, revision = admission_api
    upload, _, response = complete(admission_api)
    job = response.json()
    assert client.post(f"/ifc-models/{job['model_id']}/data-payload", json={}).status_code == 409
    assert client.delete(f"/ifc-models/{job['model_id']}").status_code == 204
    stored = session.get(IngestionJob, job["id"])
    assert stored.cancellation_requested and stored.fencing_token == 1 and stored.status == "cancelled"
    assert client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={}).status_code == 404
    assert client.get(f"/ifc-models/jobs/{job['id']}/report").status_code == 404
    assert client.get(f"/ifc-models/{job['model_id']}").status_code == 404
    assert client.get(f"/ifc-models/{job['model_id']}/download").status_code == 404
    assert client.get("/ifc-models").json() == []
    assert (
        client.post(
            f"/ifc-models/{job['model_id']}/reprocess",
            json={
                "request_id": "c20646c7-cdb9-4ff0-b04f-b02ee133dc19",
                "profile_id": revision.content.profile_id,
                "revision_id": revision.revision_id,
            },
        ).status_code
        == 404
    )
    assert stored.raw_key in objects.values
    mock_s3.delete_object.assert_not_called()


def test_disabled_unqualified_oversized_expired_and_missing_uploads(admission_api, session):
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from app.models.models import IfcUploadSession

    client, service, _, _ = admission_api
    runtime = service.runtime
    service.runtime = replace(runtime, enabled=False)
    assert initiate(admission_api).status_code == 503
    service.runtime = replace(runtime, max_input_bytes=0)
    assert initiate(admission_api).status_code == 503
    service.runtime = replace(runtime, max_input_bytes=10)
    assert initiate(admission_api).status_code == 413
    service.runtime = runtime
    upload = initiate(admission_api).json()
    assert client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={}).status_code == 409
    stored = session.get(IfcUploadSession, upload["id"])
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.add(stored)
    session.commit()
    assert client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={}).status_code == 410
    assert client.get(f"/ifc-models/uploads/{upload['id']}").status_code == 410


def test_private_neo_download_reads_verified_raw_bytes_without_shared_temporary_filename(admission_api, mock_s3):
    client, _, _, _ = admission_api
    _, _, response = complete(admission_api)
    downloaded = client.get(f"/ifc-models/{response.json()['model_id']}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == IFC
    mock_s3.download_file.assert_not_called()


def test_cancellation_is_idempotent_and_cannot_cancel_published_result(admission_api, session):
    from app.models.models import IngestionJob

    client, _, _, _ = admission_api
    _, _, response = complete(admission_api)
    job = response.json()
    response = client.post(f"/ifc-models/jobs/{job['id']}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert client.post(f"/ifc-models/jobs/{job['id']}/cancel").status_code == 200
    stored = session.get(IngestionJob, job["id"])
    assert stored.fencing_token == 1
    stored.status = "succeeded"
    session.add(stored)
    session.commit()
    assert client.post(f"/ifc-models/jobs/{job['id']}/cancel").status_code == 409


def test_runtime_changes_require_new_upload_and_retry_cannot_overlap_reprocessing(admission_api, session):
    from dataclasses import replace

    from app.models.models import IngestionJob

    client, service, _, revision = admission_api
    upload = initiate(admission_api).json()
    runtime = service.runtime
    service.runtime = replace(runtime, max_input_bytes=8191)
    assert client.post(f"/ifc-models/uploads/{upload['id']}/complete", json={}).status_code == 409
    service.runtime = runtime
    _, _, response = complete(admission_api)
    job = response.json()
    stored = session.get(IngestionJob, job["id"])
    stored.status, stored.retryable = "failed", True
    session.add(stored)
    session.commit()
    reprocess = {
        "request_id": "c20646c7-cdb9-4ff0-b04f-b02ee133dc19",
        "profile_id": revision.content.profile_id,
        "revision_id": revision.revision_id,
    }
    assert client.post(f"/ifc-models/{stored.model_id}/reprocess", json=reprocess).status_code == 202
    retry = {"request_id": "87c2fa11-4ba8-46e3-a84a-c2f5d6344593", "expected_dispatch_generation": 1}
    assert client.post(f"/ifc-models/jobs/{stored.id}/retry", json=retry).status_code == 409


@pytest.mark.parametrize(
    "changes",
    [
        {"filename": "../private.ifc"},
        {"filename": "bad\n.ifc"},
        {"size_bytes": True},
        {"size_bytes": 0},
        {"sha256": "unverified"},
        {"raw_key": "arbitrary"},
    ],
)
def test_upload_contract_rejects_unsafe_input(admission_api, changes):
    assert initiate(admission_api, **changes).status_code == 422


def test_concurrent_completion_replays_one_durable_job(admission_api, test_user, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlmodel import Session, SQLModel, create_engine

    from app.ingestion.admission import IngestionAdmission
    from app.models.models import IngestionJob, IngestionOutbox
    from app.schemas.models import IfcUploadInitiate

    _, service, objects, revision = admission_api
    engine = create_engine(f"sqlite:///{tmp_path / 'admission.sqlite'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        admission = IngestionAdmission(db, service.profiles, service.objects, service.runtime)
        upload = admission.initiate(
            IfcUploadInitiate(
                request_id="d29dd26e-b135-49f9-bb62-02e3c09843ab",
                filename="building.ifc",
                size_bytes=len(IFC),
                sha256=hashlib.sha256(IFC).hexdigest(),
                profile_id=revision.content.profile_id,
                revision_id=revision.revision_id,
            ),
            test_user,
        )
        staging_key = upload.upload_url.removeprefix("https://private-storage.test/")
        objects.values[staging_key] = IFC
    barrier = Barrier(2, timeout=5)
    original = service.objects.read_verified

    def read(key, size, sha256):
        data = original(key, size, sha256)
        if key == staging_key:
            barrier.wait()
        return data

    monkeypatch.setattr(service.objects, "read_verified", read)

    def finish(_):
        with Session(engine) as db:
            admission = IngestionAdmission(db, service.profiles, service.objects, service.runtime)
            return admission.complete(upload.id, test_user).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(finish, range(2)))
    assert ids[0] == ids[1]
    with Session(engine) as db:
        assert len(db.exec(select(IngestionJob)).all()) == 1
        assert len(db.exec(select(IngestionOutbox)).all()) == 1
