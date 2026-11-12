"""The /admin API: authorization, user listing/search/sorting, detail, deactivation, overview, storage and system."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from app.api import admin as admin_api
from app.auth import admin as admin_auth
from app.auth.admin import get_verified_claims
from app.core.config import settings
from app.main import app
from app.models.models import IdsFile, IfcModel, StorageUsage, StorageUsagePrefix, StorageUsageUser, User, UserActivity
from app.storage.reconcile import ReconcileInProgressError

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _reset_admin_caches():
    admin_auth.reset_admin_caches()
    yield
    admin_auth.reset_admin_caches()


@pytest.fixture
def admin_client(client):
    """The shared client (test_user) with a verified token that carries the admin group."""
    app.dependency_overrides[get_verified_claims] = lambda: {"sub": "test-sub", "groups": [settings.ADMIN_GROUP]}
    return client


def _user(session, name, *, email=None, created_days_ago=0, active=True) -> User:
    user = User(
        authentik_sub=f"sub-{name}",
        username=name,
        email=email or f"{name}@example.org",
        is_active=active,
        created_at=NOW - timedelta(days=created_days_ago),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _seen(session, user, days_ago):
    session.add(UserActivity(user_id=user.id, last_seen_at=NOW - timedelta(days=days_ago)))
    session.commit()


def _usage(session, user, used_bytes, objects=1):
    session.add(StorageUsageUser(user_id=user.id, used_bytes=used_bytes, object_count=objects))
    session.commit()


def _model(session, user, model_id, *, processed=False, status="pending", days_ago=0):
    session.add(
        IfcModel(
            id=model_id,
            filename=f"{model_id}.ifc",
            s3_key=f"models/{model_id}/{model_id}.ifc",
            user_id=user.id,
            processed=processed,
            processing_status=status,
            created_at=NOW - timedelta(days=days_ago),
        )
    )
    session.commit()


def _ids(session, user, ids_id):
    session.add(IdsFile(id=ids_id, filename=f"{ids_id}.ids", s3_key=f"ids/{ids_id}/{ids_id}.ids", user_id=user.id))
    session.commit()


@pytest.fixture
def population(session, test_user):
    """test_user (admin) plus alice, bob and carol with models, IDS files, activity and storage."""
    alice = _user(session, "alice", created_days_ago=2)
    bob = _user(session, "bob", email="bob@corp.example", created_days_ago=20, active=False)
    carol = _user(session, "carol_c", created_days_ago=90)
    _model(session, alice, "a1", processed=True, status="processing")
    _model(session, alice, "a2", status="processing", days_ago=1)
    _model(session, bob, "b1")
    _ids(session, alice, "ia1")
    _seen(session, alice, 1)
    _seen(session, carol, 10)
    _usage(session, alice, 5000, 3)
    _usage(session, carol, 9000, 2)
    return {"alice": alice, "bob": bob, "carol": carol, "me": test_user}


# --- authorization ---------------------------------------------------------------------------------------------------

ADMIN_ROUTES = [
    ("GET", "/admin/overview"),
    ("GET", "/admin/users"),
    ("GET", "/admin/users/some-id"),
    ("PATCH", "/admin/users/some-id"),
    ("GET", "/admin/storage"),
    ("POST", "/admin/storage/reconcile"),
    ("GET", "/admin/system"),
]


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_every_admin_route_rejects_non_admins(client, method, path):
    app.dependency_overrides[get_verified_claims] = lambda: {"sub": "test-sub", "groups": ["users"]}
    response = client.request(method, path, json={"is_active": False})
    assert response.status_code == 403
    assert response.json() == {"detail": "Admin privileges required", "code": "FORBIDDEN"}


def test_admin_routes_are_tagged_and_secured_in_openapi():
    schema = app.openapi()
    for method, path in ADMIN_ROUTES:
        template = path.replace("some-id", "{user_id}")
        operation = schema["paths"][template][method.lower()]
        assert operation["tags"] == ["admin"]
        assert operation["security"] == [{"HTTPBearer": []}]
    assert "AdminUser" in schema["components"]["schemas"]
    assert "StorageSummary" in schema["components"]["schemas"]


def test_reconcile_is_an_expensive_route():
    from app.core.rate_limit import is_expensive_route

    assert is_expensive_route("POST", "/admin/storage/reconcile")
    assert not is_expensive_route("GET", "/admin/storage")


# --- users -----------------------------------------------------------------------------------------------------------


def test_list_users_defaults_to_newest_first_with_aggregates(admin_client, population):
    response = admin_client.get("/admin/users")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert [item["username"] for item in body["items"]] == ["testuser", "alice", "bob", "carol_c"]
    alice = body["items"][1]
    assert alice["ifc_models"] == 2
    assert alice["ids_files"] == 1
    assert alice["storage_bytes"] == 5000
    assert alice["storage_objects"] == 3
    assert alice["last_seen_at"] is not None
    assert alice["is_admin"] is None  # never seen by this process
    me = body["items"][0]
    assert me["is_admin"] is True  # determined for this request
    assert me["storage_bytes"] == 0
    assert me["last_seen_at"] is None
    assert body["items"][2]["is_active"] is False


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        ("storage_bytes", "desc", ["carol_c", "alice"]),
        ("username", "asc", ["alice", "bob", "carol_c", "testuser"]),
        ("username", "desc", ["testuser", "carol_c", "bob", "alice"]),
        ("created_at", "asc", ["carol_c", "bob", "alice", "testuser"]),
        # never-seen users always go last
        ("last_seen_at", "desc", ["alice", "carol_c"]),
        ("last_seen_at", "asc", ["carol_c", "alice"]),
    ],
)
def test_list_users_sorting(admin_client, population, sort, order, expected):
    items = admin_client.get("/admin/users", params={"sort": sort, "order": order}).json()["items"]
    assert [item["username"] for item in items][: len(expected)] == expected


def test_list_users_search_matches_username_or_email(admin_client, population):
    by_email = admin_client.get("/admin/users", params={"search": "CORP.example"}).json()
    assert by_email["total"] == 1
    assert by_email["items"][0]["username"] == "bob"

    by_name = admin_client.get("/admin/users", params={"search": "ali"}).json()
    assert [item["username"] for item in by_name["items"]] == ["alice"]


def test_list_users_search_treats_wildcards_literally(admin_client, population):
    assert admin_client.get("/admin/users", params={"search": "%"}).json()["total"] == 0
    underscore = admin_client.get("/admin/users", params={"search": "_"}).json()
    assert [item["username"] for item in underscore["items"]] == ["carol_c"]


def test_list_users_pagination(admin_client, population):
    body = admin_client.get("/admin/users", params={"sort": "username", "order": "asc", "limit": 2, "offset": 1}).json()
    assert body["total"] == 4
    assert [item["username"] for item in body["items"]] == ["bob", "carol_c"]


@pytest.mark.parametrize(
    "params",
    [{"limit": 201}, {"limit": 0}, {"offset": -1}, {"sort": "email"}, {"order": "sideways"}],
)
def test_list_users_rejects_bad_parameters(admin_client, params):
    assert admin_client.get("/admin/users", params=params).status_code == 422


def test_list_users_uses_a_constant_number_of_queries(admin_client, population, session, engine):
    statements = []

    def count(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    for index in range(20):
        _model(session, _user(session, f"extra{index}"), f"extra-model-{index}")
    event.listen(engine, "before_cursor_execute", count)
    try:
        response = admin_client.get("/admin/users", params={"limit": 200})
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert response.json()["total"] == 24
    # the page, the total, and the auth/admin plumbing; never one per user
    assert len(statements) <= 3


def test_user_detail(admin_client, population, mock_s3):
    alice = population["alice"]
    listings = {
        f"lexicon/profiles/{alice.id}/": [(f"lexicon/profiles/{alice.id}/p.json", 100)],
        "models/a1/": [("models/a1/a1.ifc", 4000)],
        "results/a1/": [("results/a1/stats.json", 10)],
        "ids/ia1/": [("ids/ia1/ia1.ids", 890)],
    }

    def list_objects_v2(**kwargs):
        contents = [{"Key": key, "Size": size} for key, size in listings.get(kwargs.get("Prefix"), [])]
        return {"Contents": contents, "IsTruncated": False}

    mock_s3.list_objects_v2.side_effect = list_objects_v2

    response = admin_client.get(f"/admin/users/{alice.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert [m["id"] for m in body["recent_models"]] == ["a1", "a2"]
    assert body["recent_models"][0]["processed"] is True
    assert [i["id"] for i in body["recent_ids_files"]] == ["ia1"]
    assert body["storage_by_prefix"] == [
        {"prefix": "models", "bytes": 4000, "objects": 1},
        {"prefix": "ids", "bytes": 890, "objects": 1},
        {"prefix": "lexicon", "bytes": 100, "objects": 1},
        {"prefix": "results", "bytes": 10, "objects": 1},
    ]
    assert body["storage_by_prefix_complete"] is True
    listed = {call.kwargs.get("Prefix") for call in mock_s3.list_objects_v2.call_args_list}
    assert {"models/a1/", "models/a2/", "results/a2/", "ids/ia1/", f"rulebuilder/workspaces/{alice.id}/"} <= listed


def test_user_detail_survives_s3_errors(admin_client, population, mock_s3):
    mock_s3.list_objects_v2.side_effect = RuntimeError("s3 down")
    body = admin_client.get(f"/admin/users/{population['alice'].id}").json()
    assert body["storage_by_prefix"] == []
    assert body["storage_by_prefix_complete"] is False


def test_user_detail_breakdown_is_capped(admin_client, population, mock_s3, monkeypatch):
    monkeypatch.setattr(admin_api, "MAX_BREAKDOWN_LISTINGS", 3)
    mock_s3.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
    body = admin_client.get(f"/admin/users/{population['alice'].id}").json()
    assert body["storage_by_prefix_complete"] is False
    assert mock_s3.list_objects_v2.call_count == 3


def test_user_detail_404(admin_client):
    assert admin_client.get("/admin/users/missing").status_code == 404


# --- activation ------------------------------------------------------------------------------------------------------


def test_deactivate_and_reactivate_user(admin_client, population, session):
    alice = population["alice"]
    response = admin_client.patch(f"/admin/users/{alice.id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert response.json()["ifc_models"] == 2
    session.expire_all()
    assert session.get(User, alice.id).is_active is False

    response = admin_client.patch(f"/admin/users/{alice.id}", json={"is_active": True})
    assert response.json()["is_active"] is True


def test_admin_cannot_deactivate_themselves(admin_client, test_user, session):
    response = admin_client.patch(f"/admin/users/{test_user.id}", json={"is_active": False})
    assert response.status_code == 400
    assert response.json()["code"] == "CANNOT_DEACTIVATE_SELF"
    session.expire_all()
    assert session.get(User, test_user.id).is_active is True
    # keeping oneself active is a no-op, not an error
    assert admin_client.patch(f"/admin/users/{test_user.id}", json={"is_active": True}).status_code == 200


def test_patch_user_validation_and_404(admin_client):
    assert admin_client.patch("/admin/users/missing", json={"is_active": False}).status_code == 404
    assert admin_client.patch("/admin/users/missing", json={}).status_code == 422


# --- overview --------------------------------------------------------------------------------------------------------


def test_overview_counts(admin_client, population, session):
    session.add(StorageUsage(scope="global", used_bytes=14000, object_count=5))
    session.add(StorageUsagePrefix(prefix="models", used_bytes=14000, object_count=5))
    session.commit()

    response = admin_client.get("/admin/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["users"] == {"total": 4, "active": 3, "new_7d": 2, "new_30d": 3, "active_7d": 1}
    assert body["content"] == {
        "ifc_models": 3,
        "ids_files": 1,
        "models_pending": 1,
        "models_processing": 1,
        "models_processed": 1,
    }
    assert body["storage"]["used_bytes"] == 14000
    assert body["storage"]["limit_bytes"] == settings.STORAGE_HARD_LIMIT_BYTES
    assert body["storage"]["by_prefix"] == [{"prefix": "models", "bytes": 14000, "objects": 5}]
    assert body["generated_at"]


def test_overview_on_empty_database(admin_client, session, test_user):
    session.delete(test_user)
    session.commit()
    body = admin_client.get("/admin/overview").json()
    assert body["users"]["total"] == 0
    assert body["content"]["ifc_models"] == 0
    assert body["storage"]["used_bytes"] == 0


# --- storage ---------------------------------------------------------------------------------------------------------


def test_storage_with_top_users(admin_client, population, session):
    session.add(StorageUsage(scope="global", used_bytes=20000, object_count=8))
    session.add(StorageUsageUser(user_id="deleted-user", used_bytes=6000, object_count=3))
    session.commit()

    body = admin_client.get("/admin/storage", params={"top": 2}).json()
    assert body["used_bytes"] == 20000
    assert body["usage_ratio"] == pytest.approx(20000 / settings.STORAGE_HARD_LIMIT_BYTES)
    assert body["top_users"] == [
        {
            "user_id": population["carol"].id,
            "username": "carol_c",
            "email": "carol_c@example.org",
            "bytes": 9000,
            "objects": 2,
        },
        {"user_id": "deleted-user", "username": None, "email": None, "bytes": 6000, "objects": 3},
    ]


def test_reconcile_runs_synchronously(admin_client, mock_s3):
    mock_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "graphs/buildings/a.ttl", "Size": 300}, {"Key": "models/x/x.ifc", "Size": 700}],
        "IsTruncated": False,
    }
    response = admin_client.post("/admin/storage/reconcile")
    assert response.status_code == 200
    body = response.json()
    assert body["used_bytes"] == 1000
    assert body["object_count"] == 2
    assert body["last_reconciled_at"] is not None


def test_reconcile_conflict_and_failure(admin_client, monkeypatch):
    def busy():
        raise ReconcileInProgressError("busy")

    monkeypatch.setattr(admin_api, "reconcile_storage", busy)
    response = admin_client.post("/admin/storage/reconcile")
    assert response.status_code == 409
    assert response.json()["code"] == "RECONCILE_IN_PROGRESS"

    def broken():
        raise RuntimeError("secret s3 detail")

    monkeypatch.setattr(admin_api, "reconcile_storage", broken)
    response = admin_client.post("/admin/storage/reconcile")
    assert response.status_code == 502
    assert response.json() == {"detail": "Storage reconcile failed", "code": "RECONCILE_FAILED"}


# --- system ----------------------------------------------------------------------------------------------------------


def test_system(admin_client, mock_celery, monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    mock_celery.control.ping.return_value = [{"worker": {"ok": "pong"}}]
    body = admin_client.get("/admin/system").json()
    assert body["build"]["git_commit"] == "abc1234"
    assert body["environment"] == settings.ENVIRONMENT
    assert body["checks"] == {"database": "ok", "s3": "ok", "celery": "ok", "authentik": "ok"}
    assert body["limits"] == {
        "storage_hard_limit_bytes": settings.STORAGE_HARD_LIMIT_BYTES,
        "storage_user_quota_bytes": settings.STORAGE_USER_QUOTA_BYTES,
        "max_upload_bytes": settings.MAX_UPLOAD_BYTES,
        "max_json_body_bytes": settings.MAX_JSON_BODY_BYTES,
        "rate_limit_default": settings.RATE_LIMIT_DEFAULT,
        "rate_limit_expensive": settings.RATE_LIMIT_EXPENSIVE,
        "rate_limit_anon": settings.RATE_LIMIT_ANON,
        "rate_limit_enabled": settings.RATE_LIMIT_ENABLED,
    }


def test_system_reports_failing_dependencies(admin_client, mock_celery):
    mock_celery.control.ping.return_value = []
    response = admin_client.get("/admin/system")
    assert response.status_code == 200
    assert response.json()["checks"]["celery"] == "no workers responding"
