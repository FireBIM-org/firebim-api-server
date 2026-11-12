from __future__ import annotations

import io
import threading
from io import BytesIO
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import create_engine
from sqlmodel import Session, select

from app.core.config import settings
from app.models.models import IfcModel, User
from app.ontologies.sync_jobs import OntologySyncJobStore
from app.schemas.ontologies import OntologySyncRequest
from app.storage import quota
from app.storage.graph_store import GraphStoreClient
from app.storage.lexicon_handshake_repository import LexiconHandshakeRepository
from app.storage.lexicon_mapping_repository import LexiconMappingRepository
from app.storage.lexicon_profile_repository import LexiconProfileRepository
from app.storage.ontology_candidate_repository import OntologyCandidateRepository
from app.storage.rulebuilder_workspace_repository import RuleBuilderWorkspaceRepository
from app.storage.s3_json_store import UserScopedS3JsonStore

TTL = '@prefix ex: <http://example.org/> .\nex:a ex:b "c" .\n'


class FakeS3Client:
    """Just enough of boto3's S3 client for the accounting paths: sizes come from the stored bodies."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **_kwargs):
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(self.objects[Key])}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key]), "Metadata": {}}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)


@pytest.fixture()
def limits(monkeypatch):
    def set_limits(*, hard: int = 0, user: int = 0) -> None:
        monkeypatch.setattr(settings, "STORAGE_HARD_LIMIT_BYTES", hard)
        monkeypatch.setattr(settings, "STORAGE_USER_QUOTA_BYTES", user)

    set_limits(hard=1_000_000)
    return set_limits


@pytest.fixture()
def rejections(monkeypatch):
    reasons: list[str] = []
    monkeypatch.setattr(quota.metrics, "record_storage_rejected", reasons.append)
    return reasons


def _prefix_usage(prefix: str) -> tuple[int, int]:
    for item in quota.get_storage_summary().by_prefix:
        if item.prefix == prefix:
            return item.bytes, item.objects
    return 0, 0


# --- key layout ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "prefix"),
    [
        ("models/abc/file.ifc", "models"),
        ("ids/abc/file.ids", "ids"),
        ("graphs/buildings/x.ttl", "graphs"),
        ("results/m1/stats.json", "results"),
        ("checks/c1/result.json", "checks"),
        ("lexicon/mappings/u1/d.json", "lexicon"),
        ("rulebuilder/workspaces/u1/w.json", "rulebuilder"),
        ("ontologies/manifests/ifc/global.json", "ontologies"),
        ("ontology-sync/jobs/j.json", "ontology-sync"),
        ("tmp/upload.bin", "other"),
        ("toplevel.txt", "other"),
    ],
)
def test_prefix_for_key(key, prefix):
    assert quota.prefix_for_key(key) == prefix


def test_user_scoped_roots_cover_every_json_repository():
    roots = {
        LexiconHandshakeRepository.ROOT_PREFIX,
        LexiconMappingRepository.ROOT_PREFIX,
        LexiconProfileRepository.ROOT_PREFIX,
        OntologyCandidateRepository.ROOT_PREFIX,
        RuleBuilderWorkspaceRepository.ROOT_PREFIX,
        RuleBuilderWorkspaceRepository.GROUPS_PREFIX,
    }
    assert roots == set(quota.USER_SCOPED_ROOTS)
    for root in roots:
        assert quota.prefix_for_key(f"{root}/u/d.json") in quota.STORAGE_PREFIXES


def test_user_id_from_scoped_key():
    assert quota.user_id_from_scoped_key("lexicon/profiles/user%401/p.json") == "user@1"
    assert quota.user_id_from_scoped_key("rulebuilder/rule-element-groups/u2/g.json") == "u2"
    assert quota.user_id_from_scoped_key("lexicon/profiles/p.json") is None
    assert quota.user_id_from_scoped_key("graphs/buildings/x.ttl") is None


# --- reservations ----------------------------------------------------------------------------------------------------


def test_reserve_allows_exactly_the_limit_and_refuses_one_byte_more(limits, rejections):
    limits(hard=100)

    quota.reserve(60, prefix="models")
    quota.reserve(40, prefix="graphs")
    with pytest.raises(quota.StorageLimitExceeded) as exc_info:
        quota.reserve(1, prefix="graphs")

    assert exc_info.value.status_code == 507
    assert exc_info.value.code == "STORAGE_LIMIT_EXCEEDED"
    assert rejections == ["storage_limit"]
    summary = quota.get_storage_summary()
    assert (summary.used_bytes, summary.object_count) == (100, 2)
    assert summary.usage_ratio == 1.0
    assert _prefix_usage("graphs") == (40, 1)


def test_second_reservation_that_does_not_fit_is_refused_until_the_first_is_released(limits):
    limits(hard=100)

    first = quota.reserve(70, prefix="models", user_id="u1")
    with pytest.raises(quota.StorageLimitExceeded):
        quota.reserve(70, prefix="models", user_id="u2")

    first.release()
    first.release()  # idempotent
    assert quota.get_storage_summary().used_bytes == 0
    assert quota.get_user_usage("u1").bytes == 0
    quota.reserve(70, prefix="models", user_id="u2")
    assert quota.get_user_usage("u2").bytes == 70


def test_concurrent_reservations_never_exceed_the_limit(limits, tmp_path):
    limits(hard=100)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'accounting.db'}", connect_args={"check_same_thread": False, "timeout": 30}
    )
    quota.set_accounting_engine(engine)
    quota.ensure_accounting_tables(engine)
    granted: list[int] = []
    refused: list[int] = []
    barrier = threading.Barrier(20)

    def writer(index: int) -> None:
        barrier.wait()
        try:
            quota.reserve(10, prefix="models", user_id=f"u{index % 3}")
            granted.append(index)
        except quota.StorageLimitExceeded:
            refused.append(index)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(granted) == 10
    assert len(refused) == 10
    summary = quota.get_storage_summary()
    assert (summary.used_bytes, summary.object_count) == (100, 10)
    assert sum(user.bytes for user in quota.get_top_users()) == 100
    engine.dispose()


def test_user_quota_is_enforced_per_user_and_rolls_back_the_global_counter(limits, rejections):
    limits(hard=1_000, user=50)

    quota.reserve(40, prefix="models", user_id="u1")
    with pytest.raises(quota.UserQuotaExceeded) as exc_info:
        quota.reserve(20, prefix="models", user_id="u1")

    assert exc_info.value.status_code == 507
    assert exc_info.value.code == "USER_QUOTA_EXCEEDED"
    assert rejections == ["user_quota"]
    assert quota.get_storage_summary().used_bytes == 40
    assert quota.get_user_usage("u1").bytes == 40
    # other users and global (unattributed) objects are unaffected
    quota.reserve(20, prefix="models", user_id="u2")
    quota.reserve(20, prefix="graphs")
    assert quota.get_storage_summary().used_bytes == 80


def test_users_are_accounted_even_without_a_user_quota(limits):
    limits(hard=1_000, user=0)

    quota.reserve(600, prefix="models", user_id="u1")
    quota.reserve(300, prefix="ids", user_id="u1")

    usage = quota.get_user_usage("u1")
    assert (usage.bytes, usage.objects) == (900, 2)
    assert [user.user_id for user in quota.get_top_users()] == ["u1"]


def test_shrinking_overwrite_is_allowed_over_the_limit(limits):
    limits(hard=100)
    quota.reserve(100, prefix="graphs")
    limits(hard=50)  # lowered below current usage

    quota.reserve(-30, prefix="graphs", object_delta=0)
    with pytest.raises(quota.StorageLimitExceeded):
        quota.reserve(1, prefix="graphs")
    assert quota.get_storage_summary().used_bytes == 70


def test_deletes_decrement_and_never_go_below_zero(limits):
    quota.reserve(10, prefix="lexicon", user_id="u1")

    quota.record_delete("lexicon/mappings/u1/d.json", 25, user_id="u1")
    quota.record_delete("lexicon/mappings/u1/e.json", 5, user_id="u1")

    summary = quota.get_storage_summary()
    assert (summary.used_bytes, summary.object_count) == (0, 0)
    assert _prefix_usage("lexicon") == (0, 0)
    assert quota.get_user_usage("u1").bytes == 0


def test_hard_limit_zero_disables_the_cap(limits):
    limits(hard=0)
    quota.reserve(10**13, prefix="models")
    summary = quota.get_storage_summary()
    assert summary.used_bytes == 10**13
    assert summary.usage_ratio == 0.0


def test_accounting_failure_fails_closed(limits, rejections, tmp_path):
    limits(hard=100)
    quota.set_accounting_engine(create_engine(f"sqlite:///{tmp_path / 'missing' / 'accounting.db'}"))

    with pytest.raises(quota.StorageAccountingUnavailable) as exc_info:
        quota.reserve(1, prefix="models")

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "STORAGE_ACCOUNTING_UNAVAILABLE"
    assert rejections == ["accounting_unavailable"]
    quota.record_delete("models/x/y.ifc", 1)  # deletes never raise


def test_accounting_failure_is_tolerated_when_all_limits_are_disabled(limits, tmp_path):
    limits(hard=0, user=0)
    quota.set_accounting_engine(create_engine(f"sqlite:///{tmp_path / 'missing' / 'accounting.db'}"))

    reservation = quota.reserve(1, prefix="models")

    assert reservation.applied is False
    reservation.release()


def test_crossing_the_warning_ratio_logs_once(limits, monkeypatch, caplog):
    limits(hard=100)
    monkeypatch.setattr(settings, "STORAGE_WARN_RATIO", 0.8)

    quota.reserve(70, prefix="models")
    quota.reserve(15, prefix="models")
    quota.reserve(5, prefix="models")

    warnings = [r for r in caplog.records if "warning threshold" in r.getMessage()]
    assert len(warnings) == 1


def test_storage_summary_shape(limits):
    limits(hard=200, user=50)
    quota.reserve(50, prefix="models")

    summary = quota.get_storage_summary().model_dump()

    assert set(summary) == {
        "used_bytes",
        "object_count",
        "limit_bytes",
        "user_quota_bytes",
        "usage_ratio",
        "warn_ratio",
        "last_reconciled_at",
        "by_prefix",
    }
    assert summary["limit_bytes"] == 200
    assert summary["user_quota_bytes"] == 50
    assert summary["usage_ratio"] == 0.25
    assert summary["last_reconciled_at"] is None
    assert summary["by_prefix"] == [{"prefix": "models", "bytes": 50, "objects": 1}]


# --- S3 helpers and stores -------------------------------------------------------------------------------------------


def test_account_write_counts_only_the_size_difference_of_an_overwrite(limits):
    client = FakeS3Client()
    key = "results/m1/stats.json"

    for body in (b"x" * 100, b"y" * 150, b"z" * 30):
        with quota.account_write(client, "bucket", key, len(body), user_id="u1"):
            client.put_object(Bucket="bucket", Key=key, Body=body)

    summary = quota.get_storage_summary()
    assert (summary.used_bytes, summary.object_count) == (30, 1)
    assert quota.get_user_usage("u1").objects == 1


def test_account_write_overwrite_is_refused_only_for_the_growth(limits):
    limits(hard=120)
    client = FakeS3Client()
    key = "graphs/buildings/g.ttl"
    with quota.account_write(client, "bucket", key, 100):
        client.put_object(Bucket="bucket", Key=key, Body=b"x" * 100)

    with quota.account_write(client, "bucket", key, 120):  # +20 fits
        client.put_object(Bucket="bucket", Key=key, Body=b"x" * 120)
    with pytest.raises(quota.StorageLimitExceeded), quota.account_write(client, "bucket", key, 121):
        client.put_object(Bucket="bucket", Key=key, Body=b"x" * 121)

    assert client.objects[key] == b"x" * 120
    assert quota.get_storage_summary().used_bytes == 120


def test_account_write_releases_the_reservation_when_the_write_fails(limits):
    client = FakeS3Client()

    with pytest.raises(RuntimeError), quota.account_write(client, "bucket", "models/a/b.ifc", 500, user_id="u1"):
        raise RuntimeError("upload failed")

    summary = quota.get_storage_summary()
    assert (summary.used_bytes, summary.object_count) == (0, 0)
    assert quota.get_user_usage("u1").bytes == 0


def test_delete_object_accounted_uses_the_object_size(limits):
    client = FakeS3Client()
    with quota.account_write(client, "bucket", "models/a/b.ifc", 64, user_id="u1", new_object=True):
        client.put_object(Bucket="bucket", Key="models/a/b.ifc", Body=b"x" * 64)

    assert quota.delete_object_accounted(client, "bucket", "models/a/b.ifc", user_id="u1") is True
    assert quota.delete_object_accounted(client, "bucket", "models/a/b.ifc", user_id="u1") is False

    assert quota.get_storage_summary().used_bytes == 0
    assert "models/a/b.ifc" not in client.objects


def test_reserve_upload_is_the_hook_for_presigned_uploads(limits):
    limits(hard=1_000, user=600)

    reservation = quota.reserve_upload("models/new/file.ifc", 500, user_id="u1")
    with pytest.raises(quota.UserQuotaExceeded):
        quota.reserve_upload("models/other/file.ifc", 200, user_id="u1")
    reservation.release()  # upload abandoned

    assert quota.get_storage_summary().used_bytes == 0


def test_graph_store_accounts_publish_overwrite_and_delete(limits):
    store = GraphStoreClient(bucket_name="bucket", s3_client=FakeS3Client())
    uri = "https://firebim.org/graphs/buildings/demo"

    store.put_graph_ttl("buildings", uri, TTL)
    store.put_graph_ttl("buildings", uri, TTL + "ex:d ex:e ex:f .\n")
    assert _prefix_usage("graphs") == (len(TTL) + len("ex:d ex:e ex:f .\n"), 1)

    store.delete_graph("buildings", uri)
    assert _prefix_usage("graphs") == (0, 0)
    assert quota.get_top_users() == []


def test_graph_store_publish_over_the_limit_writes_nothing(limits):
    limits(hard=10)
    client = FakeS3Client()
    store = GraphStoreClient(bucket_name="bucket", s3_client=client)

    with pytest.raises(quota.StorageLimitExceeded):
        store.put_graph_ttl("buildings", "https://firebim.org/graphs/buildings/demo", TTL)
    assert client.objects == {}


def test_user_scoped_json_store_attributes_documents_to_the_user(limits):
    store = UserScopedS3JsonStore(bucket_name="bucket", s3_client=FakeS3Client())
    user = SimpleNamespace(id="user-1")

    store.write_dict("lexicon/mappings", user, "doc-1", {"a": 1}, write_error_message="write failed")
    size = quota.get_user_usage("user-1").bytes
    assert size == len(b'{\n  "a": 1\n}')
    assert _prefix_usage("lexicon") == (size, 1)

    store.delete_document(
        "lexicon/mappings", user, "doc-1", not_found_message="missing", delete_error_message="delete failed"
    )
    assert quota.get_user_usage("user-1").bytes == 0
    assert quota.get_storage_summary().used_bytes == 0


def test_sync_job_documents_are_attributed_to_their_owner(limits):
    client = FakeS3Client()
    store = OntologySyncJobStore(client=client, bucket_name="bucket")

    record = store.create(owner_id="owner-1", request=OntologySyncRequest())
    store.update(record["job_id"], status="running")

    stored = len(next(iter(client.objects.values())))
    usage = quota.get_user_usage("owner-1")
    assert (usage.bytes, usage.objects) == (stored, 1)
    assert _prefix_usage("ontology-sync") == (stored, 1)


# --- API -------------------------------------------------------------------------------------------------------------


def test_upload_model_is_accounted_to_the_user(client, mock_s3, test_user: User, limits):
    content = b"IFC CONTENT"

    response = client.post("/ifc-models", files={"file": ("m.ifc", io.BytesIO(content))})

    assert response.status_code == 201
    assert quota.get_user_usage(test_user.id).bytes == len(content)
    assert _prefix_usage("models") == (len(content), 1)


def test_upload_model_over_the_storage_limit_returns_507(client, mock_s3, session: Session, limits):
    limits(hard=10)

    response = client.post("/ifc-models", files={"file": ("big.ifc", io.BytesIO(b"x" * 11))})

    assert response.status_code == 507
    assert response.json()["code"] == "STORAGE_LIMIT_EXCEEDED"
    mock_s3.upload_fileobj.assert_not_called()
    assert session.exec(select(IfcModel)).all() == []
    assert quota.get_storage_summary().used_bytes == 0


def test_upload_ids_over_the_user_quota_returns_507(client, mock_s3, limits):
    limits(hard=1_000, user=10)

    response = client.post("/ids-files", files={"file": ("big.ids", io.BytesIO(b"<ids>" * 3))})

    assert response.status_code == 507
    assert response.json() == {"detail": quota.UserQuotaExceeded().detail, "code": "USER_QUOTA_EXCEEDED"}
    mock_s3.upload_fileobj.assert_not_called()


def test_failed_upload_releases_its_reservation(client, mock_s3, limits):
    mock_s3.upload_fileobj.side_effect = ClientError({"Error": {"Code": "SlowDown"}}, "PutObject")

    response = client.post("/ifc-models", files={"file": ("m.ifc", io.BytesIO(b"IFC"))})

    assert response.status_code == 500
    assert quota.get_storage_summary().used_bytes == 0


def test_upload_is_refused_when_accounting_is_unavailable(client, mock_s3, limits, tmp_path):
    quota.set_accounting_engine(create_engine(f"sqlite:///{tmp_path / 'missing' / 'accounting.db'}"))

    response = client.post("/ifc-models", files={"file": ("m.ifc", io.BytesIO(b"IFC"))})

    assert response.status_code == 503
    assert response.json()["code"] == "STORAGE_ACCOUNTING_UNAVAILABLE"
    mock_s3.upload_fileobj.assert_not_called()


def test_delete_model_releases_its_bytes(client, mock_s3, test_user: User, limits):
    content = b"IFC CONTENT"
    model_id = client.post("/ifc-models", files={"file": ("m.ifc", io.BytesIO(content))}).json()["id"]
    mock_s3.head_object.return_value = {"ContentLength": len(content)}

    response = client.delete(f"/ifc-models/{model_id}")

    assert response.status_code == 204
    mock_s3.delete_object.assert_called_once()
    assert quota.get_user_usage(test_user.id).bytes == 0
    assert quota.get_storage_summary().object_count == 0


def test_graph_publish_over_the_limit_returns_507(client, monkeypatch, limits):
    from app.api import graphs as graphs_api

    limits(hard=10)
    monkeypatch.setattr(graphs_api.repo, "client", GraphStoreClient(bucket_name="bucket", s3_client=FakeS3Client()))

    response = client.post("/graphs/publish", json={"graph_id": "demo", "ttl": TTL})

    assert response.status_code == 507
    assert response.json()["code"] == "STORAGE_LIMIT_EXCEEDED"


def test_rule_publish_over_the_limit_returns_507_instead_of_a_soft_storage_error(client, monkeypatch, limits):
    from app.rules import publisher

    limits(hard=10)
    monkeypatch.setattr(publisher, "_client", GraphStoreClient(bucket_name="bucket", s3_client=FakeS3Client()))
    rule = {
        "id": "quota-rule",
        "entry": "e1",
        "elements": [
            {
                "id": "e1",
                "element_type": "decision",
                "target_class": "https://example.org/Wall",
                "property_path": "https://example.org/height",
                "operator": ">",
                "value": 2,
            }
        ],
    }

    response = client.post("/rules/publish", json={"rule": rule})

    assert response.status_code == 507
    assert response.json()["code"] == "STORAGE_LIMIT_EXCEEDED"
