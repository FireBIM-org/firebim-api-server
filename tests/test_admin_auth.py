"""Admin determination (groups claim, userinfo fallback, ADMIN_EMAILS) and throttled user activity tracking."""

import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.auth import activity
from app.auth import admin as admin_auth
from app.auth import jwt as auth_jwt
from app.auth.admin import get_verified_claims
from app.auth.jwt import get_current_active_user
from app.core.config import settings
from app.main import app
from app.models.models import User, UserActivity

ISSUER = settings.AUTHENTIK_ISSUER_URL


@pytest.fixture(autouse=True)
def _reset_caches():
    admin_auth.reset_admin_caches()
    activity.reset_activity_throttle()
    yield
    admin_auth.reset_admin_caches()
    activity.reset_activity_throttle()


def _use_claims(claims: dict) -> None:
    app.dependency_overrides[get_verified_claims] = lambda: claims


class _Userinfo:
    """MockTransport for Authentik discovery + userinfo; counts userinfo calls."""

    def __init__(self, userinfo: dict | None, *, status_code: int = 200) -> None:
        self.userinfo = userinfo
        self.status_code = status_code
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            # Authentik answers with the public host; the API must keep calling the configured origin.
            return httpx.Response(
                200, json={"userinfo_endpoint": "https://auth.public.example/application/o/userinfo/"}
            )
        self.calls.append(request)
        return httpx.Response(self.status_code, json=self.userinfo or {})

    def install(self, monkeypatch) -> None:
        transport = httpx.MockTransport(self.handler)
        monkeypatch.setattr(admin_auth, "_http_client", lambda: httpx.AsyncClient(transport=transport))


# --- admin determination via the API ---------------------------------------------------------------------------------


def test_users_me_reports_admin_from_groups_claim(client):
    _use_claims({"sub": "test-sub", "groups": ["staff", settings.ADMIN_GROUP]})
    response = client.get("/users/me")
    assert response.status_code == 200
    assert response.json()["is_admin"] is True


def test_users_me_reports_non_admin(client):
    _use_claims({"sub": "test-sub", "groups": ["staff"]})
    response = client.get("/users/me")
    assert response.status_code == 200
    assert response.json()["is_admin"] is False


def test_non_admin_gets_403(client):
    _use_claims({"sub": "test-sub", "groups": []})
    response = client.get("/admin/overview")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_admin_routes_require_authentication(client):
    app.dependency_overrides.pop(get_current_active_user, None)
    assert client.get("/admin/overview").status_code == 401


def test_userinfo_fallback_when_token_has_no_groups_claim(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTHENTIK_INTERNAL_URL", "http://authentik-server:9000/application/o/firebim/")
    userinfo = _Userinfo({"sub": "test-sub", "groups": [settings.ADMIN_GROUP]})
    userinfo.install(monkeypatch)
    _use_claims({"sub": "test-sub", "exp": int(time.time()) + 600})
    headers = {"Authorization": "Bearer opaque-token"}

    assert client.get("/admin/overview", headers=headers).status_code == 200
    assert client.get("/users/me", headers=headers).json()["is_admin"] is True

    # One userinfo call, cached per token, against the internal origin with the caller's token.
    assert len(userinfo.calls) == 1
    assert str(userinfo.calls[0].url) == "http://authentik-server:9000/application/o/userinfo/"
    assert userinfo.calls[0].headers["authorization"] == "Bearer opaque-token"


def test_userinfo_fallback_failure_denies_admin(client, monkeypatch):
    userinfo = _Userinfo(None, status_code=401)
    userinfo.install(monkeypatch)
    _use_claims({"sub": "test-sub", "exp": int(time.time()) + 600})
    headers = {"Authorization": "Bearer rejected-token"}

    assert client.get("/admin/overview", headers=headers).status_code == 403
    assert client.get("/admin/overview", headers=headers).status_code == 403
    assert len(userinfo.calls) == 1  # failures are cached briefly too


def test_userinfo_not_called_when_groups_claim_present(client, monkeypatch):
    userinfo = _Userinfo({"groups": [settings.ADMIN_GROUP]})
    userinfo.install(monkeypatch)
    _use_claims({"sub": "test-sub", "groups": []})
    assert client.get("/admin/overview", headers={"Authorization": "Bearer t"}).status_code == 403
    assert userinfo.calls == []


def test_admin_via_admin_emails(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "someone@else.org, Test@Example.org")
    _use_claims({"sub": "test-sub", "groups": [], "email": "test@example.ORG"})
    assert client.get("/admin/overview").status_code == 200


def test_admin_emails_ignored_when_address_is_shared(client, session, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "test@example.org")
    session.add(User(authentik_sub="squatter", username="squatter", email="TEST@example.org"))
    session.commit()
    _use_claims({"sub": "test-sub", "groups": [], "email": "test@example.org"})
    assert client.get("/admin/overview").status_code == 403


def test_admin_emails_requires_the_address_to_be_the_callers_own(client, session, monkeypatch):
    # No groups/email in the token, so userinfo is asked; it reports an address the caller's row was never synced to.
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "boss@example.org")
    session.add(User(authentik_sub="boss", username="boss", email="boss@example.org"))
    session.commit()
    _Userinfo({"sub": "test-sub", "groups": [], "email": "boss@example.org"}).install(monkeypatch)
    _use_claims({"sub": "test-sub", "exp": int(time.time()) + 600})
    assert client.get("/admin/overview", headers={"Authorization": "Bearer t"}).status_code == 403


def test_admin_emails_via_userinfo_email_matching_the_caller(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "test@example.org")
    _Userinfo({"sub": "test-sub", "groups": [], "email": "Test@Example.org"}).install(monkeypatch)
    _use_claims({"sub": "test-sub", "exp": int(time.time()) + 600})
    assert client.get("/admin/overview", headers={"Authorization": "Bearer t"}).status_code == 200


def test_email_not_in_admin_emails_is_not_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "boss@example.org")
    _use_claims({"sub": "test-sub", "groups": [], "email": "test@example.org"})
    assert client.get("/admin/overview").status_code == 403


# --- unit level ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_is_admin_remembers_status(test_user):
    assert admin_auth.known_admin_status(test_user.id) is None
    assert await admin_auth.resolve_is_admin(test_user, {"groups": [settings.ADMIN_GROUP]}, None) is True
    assert admin_auth.known_admin_status(test_user.id) is True
    assert await admin_auth.resolve_is_admin(test_user, {"groups": "other"}, None) is False
    assert admin_auth.known_admin_status(test_user.id) is False


@pytest.mark.asyncio
async def test_userinfo_cache_respects_token_lifetime(monkeypatch):
    calls = []

    async def fake_fetch(token):
        calls.append(token)
        return {"groups": []}

    monkeypatch.setattr(admin_auth, "fetch_userinfo", fake_fetch)
    expired = {"exp": int(time.time()) - 1}
    await admin_auth.userinfo_claims("t1", expired)
    await admin_auth.userinfo_claims("t1", expired)
    assert calls == ["t1", "t1"]  # nothing cached past the token's expiry

    live = {"exp": int(time.time()) + 3600}
    await admin_auth.userinfo_claims("t2", live)
    await admin_auth.userinfo_claims("t2", live)
    assert calls == ["t1", "t1", "t2"]


def test_ttl_lru_is_bounded_and_expires():
    now = [0.0]
    cache = admin_auth._TtlLru(max_entries=2, clock=lambda: now[0])
    cache.put("a", 1, 10)
    cache.put("b", 2, 10)
    cache.put("c", 3, 10)
    assert len(cache) == 2
    assert cache.get("a") == (False, None)
    now[0] = 11
    assert cache.get("b") == (False, None)


# --- end to end through get_current_user -----------------------------------------------------------------------------


class _FakeJwks:
    def __init__(self, public_key):
        self._key = public_key

    def get_signing_key_from_jwt(self, token):
        return type("SigningKey", (), {"key": self._key})()


@pytest.fixture
def signed_token(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth_jwt, "get_jwks_client", lambda: _FakeJwks(private_key.public_key()))

    def make(**claims):
        payload = {
            "iss": ISSUER,
            "sub": "test-sub",
            "exp": int(time.time()) + 600,
            "email": "test@example.org",
            "preferred_username": "testuser",
            **claims,
        }
        return jwt.encode(payload, private_key, algorithm="RS256")

    return make


def test_real_token_groups_claim_grants_admin_and_records_activity(client, session, test_user, signed_token):
    app.dependency_overrides.pop(get_current_active_user, None)
    headers = {"Authorization": f"Bearer {signed_token(groups=[settings.ADMIN_GROUP])}"}

    me = client.get("/users/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["is_admin"] is True
    assert client.get("/admin/overview", headers=headers).status_code == 200

    session.expire_all()
    seen = session.exec(select(UserActivity).where(UserActivity.user_id == test_user.id)).all()
    assert len(seen) == 1


def test_real_token_without_admin_group_is_forbidden(client, signed_token):
    app.dependency_overrides.pop(get_current_active_user, None)
    headers = {"Authorization": f"Bearer {signed_token(groups=['users'])}"}
    assert client.get("/users/me", headers=headers).json()["is_admin"] is False
    assert client.get("/admin/overview", headers=headers).status_code == 403


# --- activity tracking -----------------------------------------------------------------------------------------------


def test_activity_throttle_allows_one_write_per_interval():
    now = [100.0]
    throttle = activity.ActivityThrottle(interval_seconds=300, max_entries=2, clock=lambda: now[0])
    assert throttle.claim("u1") is True
    assert throttle.claim("u1") is False
    now[0] += 299
    assert throttle.claim("u1") is False
    now[0] += 2
    assert throttle.claim("u1") is True
    throttle.claim("u2")
    throttle.claim("u3")  # evicts u1 (bounded)
    assert throttle.claim("u1") is True


def test_upsert_last_seen_inserts_then_updates(engine, session):
    first = datetime(2026, 1, 1, tzinfo=UTC)
    activity.upsert_last_seen(engine, "user-1", first)
    activity.upsert_last_seen(engine, "user-1", first + timedelta(hours=1))
    rows = session.exec(select(UserActivity)).all()
    assert len(rows) == 1
    assert rows[0][0].last_seen_at.replace(tzinfo=UTC) == first + timedelta(hours=1)


@pytest.mark.asyncio
async def test_record_user_activity_is_throttled(monkeypatch):
    writes = []
    monkeypatch.setattr(activity, "upsert_last_seen", lambda engine, user_id, seen_at: writes.append(user_id))
    await activity.record_user_activity("u1")
    await activity.record_user_activity("u1")
    await activity.record_user_activity("u2")
    assert writes == ["u1", "u2"]


@pytest.mark.asyncio
async def test_record_user_activity_never_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(activity, "upsert_last_seen", boom)
    await activity.record_user_activity("u1")  # logged, not raised
    await activity.record_user_activity(None)
