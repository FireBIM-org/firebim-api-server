import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import rate_limit as rate_limit_module
from app.core.rate_limit import (
    GcraRateLimiter,
    RateLimit,
    RateLimitMiddleware,
    VerifiedTokenCache,
    is_expensive_route,
    parse_rate_limit,
    resolve_client_ip,
)

TRUSTED = rate_limit_module.parse_trusted_networks(["10.42.0.0/16", "127.0.0.1/32"])


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("value", "limit", "period"),
    [
        ("300/minute", 300, 60),
        ("20/second", 20, 1),
        ("1000/hour", 1000, 3600),
        ("5000/day", 5000, 86400),
        (" 10 / minutes ", 10, 60),
        ("10/5minutes", 10, 300),
        ("7/Hour", 7, 3600),
    ],
)
def test_parse_rate_limit(value, limit, period):
    assert parse_rate_limit(value) == RateLimit(limit=limit, period_seconds=period)


@pytest.mark.parametrize("value", ["", "300", "300/fortnight", "0/minute", "-1/minute", "abc/minute", "1/0minutes"])
def test_parse_rate_limit_rejects_garbage(value):
    with pytest.raises(ValueError):
        parse_rate_limit(value)


def test_settings_reject_invalid_rate_limit():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(RATE_LIMIT_DEFAULT="lots")


def test_gcra_allows_burst_then_refills():
    clock = FakeClock()
    limiter = GcraRateLimiter(clock=clock)
    rate = parse_rate_limit("3/minute")

    decisions = [limiter.hit([("default", "k", rate)])[0] for _ in range(3)]
    assert [d.allowed for d in decisions] == [True, True, True]
    assert [d.remaining for d in decisions] == [2, 1, 0]

    rejected = limiter.hit([("default", "k", rate)])[0]
    assert not rejected.allowed
    assert rejected.retry_after_seconds == pytest.approx(20.0)

    clock.now += 20
    assert limiter.hit([("default", "k", rate)])[0].allowed
    assert not limiter.hit([("default", "k", rate)])[0].allowed

    clock.now += 60
    assert [limiter.hit([("default", "k", rate)])[0].allowed for _ in range(4)] == [True, True, True, False]


def test_gcra_rejection_consumes_nothing():
    clock = FakeClock()
    limiter = GcraRateLimiter(clock=clock)
    general = parse_rate_limit("5/minute")
    expensive = parse_rate_limit("1/minute")

    assert all(d.allowed for d in limiter.hit([("default", "k", general), ("expensive", "k", expensive)]))
    for _ in range(3):
        decisions = limiter.hit([("default", "k", general), ("expensive", "k", expensive)])
        assert [d.allowed for d in decisions] == [True, False]
    # The rejected expensive calls did not drain the general budget: 4 cheap calls still fit.
    assert [limiter.hit([("default", "k", general)])[0].allowed for _ in range(5)] == [True] * 4 + [False]


def test_gcra_memory_is_bounded_and_idle_buckets_are_swept():
    clock = FakeClock()
    limiter = GcraRateLimiter(max_keys=10, sweep_interval_seconds=30, clock=clock)
    rate = parse_rate_limit("10/minute")
    for index in range(25):
        limiter.hit([("anon", f"ip:{index}", rate)])
    assert len(limiter) == 10

    clock.now += 61  # every bucket has fully refilled
    limiter.hit([("anon", "ip:new", rate)])
    assert len(limiter) == 1


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/ifc-models", True),
        ("POST", "/ifc-models/", True),
        ("GET", "/ifc-models", False),
        ("POST", "/ids-files", True),
        ("POST", "/ifc-models/abc/data-payload", True),
        ("POST", "/checks/run", True),
        ("POST", "/checks/run-blind", True),
        ("POST", "/checks/run-vertical", True),
        # The webapp's rulebook runner calls run-diagnostic once per rule in sequence: default budget only.
        ("POST", "/checks/run-diagnostic", False),
        ("POST", "/checks/compatibility", False),
        ("POST", "/graphs/publish", True),
        ("POST", "/graphs/lt/answers", False),
        ("POST", "/rules/publish", True),
        ("POST", "/rules/validate", False),
        ("POST", "/ontologies/sync", True),
        ("POST", "/ontologies/sync/jobs", True),
        ("POST", "/ontologies/publish", True),
        ("POST", "/ontologies/conversions/dictionary-json-to-ttl", True),
        ("POST", "/ontologies/candidates/dictionaries/c1/publish", True),
        ("POST", "/rulebuilder/workspaces/w1/export-rule-elements", True),
        ("PUT", "/rulebuilder/workspaces/w1", False),
    ],
)
def test_expensive_route_table(method, path, expected):
    assert is_expensive_route(method, path) is expected


@pytest.mark.parametrize(
    ("peer", "xff", "expected"),
    [
        ("203.0.113.9", None, "203.0.113.9"),
        # Untrusted peers cannot choose their address via X-Forwarded-For.
        ("203.0.113.9", "198.51.100.1", "203.0.113.9"),
        # Trusted proxy: right-most untrusted hop wins, spoofed left-most entries are ignored.
        ("10.42.0.7", "1.1.1.1, 198.51.100.1", "198.51.100.1"),
        ("10.42.0.7", "1.1.1.1, 198.51.100.1, 10.42.3.4", "198.51.100.1"),
        ("10.42.0.7", "198.51.100.1:5555", "198.51.100.1"),
        ("10.42.0.7", "[2001:db8::1]:443", "2001:db8::1"),
        # Only trusted hops: the left-most one is the best we have.
        ("10.42.0.7", "10.42.9.9", "10.42.9.9"),
        # Garbage in the chain: stop at the last address a trusted proxy vouched for.
        ("10.42.0.7", "1.1.1.1, not-an-ip", "10.42.0.7"),
        ("127.0.0.1", "", "127.0.0.1"),
    ],
)
def test_resolve_client_ip(peer, xff, expected):
    assert resolve_client_ip(peer, xff, TRUSTED) == expected


# Tokens the fake identity provider accepts: two tokens of the same user (e.g. before and after a refresh).
VALID_TOKENS = {"token-alice": "alice", "token-alice-refreshed": "alice", "token-bob": "bob"}


class FakeVerifier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, token: str) -> str | None:
        self.calls.append(token)
        return VALID_TOKENS.get(token)


def _app(**overrides) -> FastAPI:
    app = FastAPI()

    @app.get("/items")
    def items():
        return {"ok": True}

    @app.post("/graphs/publish")
    def publish():
        return {"ok": True}

    @app.get("/livez")
    def livez():
        return {"status": "ok"}

    kwargs = {
        "default": "3/minute",
        "expensive": "1/minute",
        "anon": "2/minute",
        "trusted_proxy_cidrs": ["10.42.0.0/16"],
        "token_verifier": FakeVerifier(),
    }
    kwargs.update(overrides)
    app.add_middleware(RateLimitMiddleware, **kwargs)
    return app


def test_anonymous_requests_are_limited_per_ip_with_429_contract(monkeypatch):
    rejected: list[str] = []
    monkeypatch.setattr(rate_limit_module.metrics, "record_rate_limited", rejected.append)
    client = TestClient(_app())

    first = client.get("/items")
    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert first.headers["X-RateLimit-Remaining"] == "1"
    assert client.get("/items").status_code == 200

    limited = client.get("/items")
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Rate limit exceeded, retry later", "code": "RATE_LIMITED"}
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.headers["X-RateLimit-Limit"] == "2"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert rejected == ["anon"]


def test_exempt_paths_are_never_limited():
    client = TestClient(_app(anon="1/minute"))
    assert all(client.get("/livez").status_code == 200 for _ in range(5))


def test_disabled_middleware_passes_everything():
    client = TestClient(_app(anon="1/minute", enabled=False))
    assert all(client.get("/items").status_code == 200 for _ in range(5))


def test_verified_tokens_are_keyed_on_the_subject_not_the_token_or_ip():
    limiter = GcraRateLimiter()
    client = TestClient(_app(limiter=limiter))
    alice = {"Authorization": "Bearer token-alice"}
    refreshed = {"Authorization": "Bearer token-alice-refreshed"}

    # A refreshed token belongs to the same user, so it does not get a fresh budget ...
    assert [client.get("/items", headers=alice).status_code for _ in range(2)] == [200, 200]
    assert [client.get("/items", headers=refreshed).status_code for _ in range(2)] == [200, 429]
    # ... and Alice's budget is not shared with Bob (same IP) or with anonymous requests.
    assert client.get("/items", headers={"Authorization": "Bearer token-bob"}).status_code == 200
    assert client.get("/items").status_code == 200

    keys = {key for _, key in limiter._tats}
    assert {"sub:alice", "sub:bob"} <= keys
    assert not any("token-" in key for key in keys)


def test_unverifiable_tokens_count_against_the_client_ip():
    """Invented tokens neither get the authenticated budget nor a fresh bucket per token, even on public routes."""
    limiter = GcraRateLimiter()
    client = TestClient(_app(limiter=limiter))

    statuses = [client.get("/items", headers={"Authorization": f"Bearer {uuid.uuid4()}"}).status_code for _ in range(4)]

    assert statuses == [200, 200, 429, 429]  # RATE_LIMIT_ANON is 2/minute here
    assert {bucket for bucket, _ in limiter._tats} == {"anon"}
    assert len(limiter) == 1


def test_without_a_verifier_every_request_is_limited_per_ip():
    client = TestClient(_app(token_verifier=None))
    headers = {"Authorization": "Bearer token-alice"}
    assert [client.get("/items", headers=headers).status_code for _ in range(3)] == [200, 200, 429]


def test_verified_tokens_are_cached_briefly():
    clock = FakeClock()
    verifier = FakeVerifier()
    cache = VerifiedTokenCache(ttl_seconds=60, max_entries=1, clock=clock)
    client = TestClient(_app(token_verifier=verifier, verified_tokens=cache, default="100/minute"))
    alice = {"Authorization": "Bearer token-alice"}

    for _ in range(3):
        assert client.get("/items", headers=alice).status_code == 200
    assert verifier.calls == ["token-alice"]

    clock.now += 61
    assert client.get("/items", headers=alice).status_code == 200
    assert verifier.calls == ["token-alice", "token-alice"]

    # Failed verifications are not cached and the cache is bounded.
    client.get("/items", headers={"Authorization": "Bearer nope"})
    client.get("/items", headers={"Authorization": "Bearer token-bob"})
    assert len(cache) == 1


def test_a_failing_verifier_falls_back_to_the_client_ip():
    async def broken(token: str) -> str | None:
        raise RuntimeError("JWKS down")

    client = TestClient(_app(token_verifier=broken))
    headers = {"Authorization": "Bearer token-alice"}
    assert [client.get("/items", headers=headers).status_code for _ in range(3)] == [200, 200, 429]


def test_expensive_routes_count_against_both_limits():
    client = TestClient(_app())
    headers = {"Authorization": "Bearer token-alice"}
    assert client.post("/graphs/publish", headers=headers).status_code == 200
    limited = client.post("/graphs/publish", headers=headers)
    assert limited.status_code == 429
    assert limited.headers["X-RateLimit-Limit"] == "1"
    # The general budget (3) lost one request to the admitted publish only.
    assert [client.get("/items", headers=headers).status_code for _ in range(3)] == [200, 200, 429]


def test_forwarded_for_only_counts_behind_trusted_proxy():
    # Behind the webapp proxy (trusted pod IP) every browser client gets its own bucket.
    proxied = TestClient(_app(anon="1/minute"), client=("10.42.0.7", 50000))
    assert proxied.get("/items", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 200
    assert proxied.get("/items", headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200
    assert proxied.get("/items", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429

    # A direct client cannot dodge its limit by inventing X-Forwarded-For values.
    direct = TestClient(_app(anon="1/minute"), client=("203.0.113.9", 50000))
    assert direct.get("/items", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 200
    assert direct.get("/items", headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 429


def test_main_app_installs_middleware_stack_in_order():
    from app.auth.token_identity import verify_token_subject
    from app.core.body_limit import BodySizeLimitMiddleware
    from app.core.security_headers import SecurityHeadersMiddleware
    from app.main import app

    classes = [middleware.cls for middleware in app.user_middleware]
    assert classes[0] is SecurityHeadersMiddleware
    assert classes.index(RateLimitMiddleware) < classes.index(BodySizeLimitMiddleware)
    rate_limit = next(m for m in app.user_middleware if m.cls is RateLimitMiddleware)
    assert rate_limit.kwargs["default"] == "300/minute"
    assert rate_limit.kwargs["trusted_proxy_cidrs"] == ["10.42.0.0/16", "10.43.0.0/16", "127.0.0.1/32"]
    assert rate_limit.kwargs["token_verifier"] is verify_token_subject
