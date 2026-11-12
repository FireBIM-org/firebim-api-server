"""Bearer token verification used by the rate limiter (app/auth/token_identity.py)."""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWK
from jwt.algorithms import RSAAlgorithm

from app.auth import token_identity
from app.core.config import settings

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, kid: str) -> PyJWK:
    data = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    data.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return PyJWK(data)


class FakeJwksClient:
    def __init__(self, keys=None, error: Exception | None = None) -> None:
        self.keys = keys if keys is not None else [_jwk(PRIVATE_KEY, "k1")]
        self.error = error
        self.calls = 0

    def get_signing_keys(self, refresh: bool = False):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.keys


def _token(*, key=PRIVATE_KEY, kid="k1", **claims) -> str:
    payload = {
        "sub": "user-1",
        "iss": settings.AUTHENTIK_ISSUER_URL,
        "exp": int(time.time()) + 300,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture(autouse=True)
def _reset_cooldown(monkeypatch):
    monkeypatch.setattr(token_identity, "_jwks_unavailable_until", 0.0)


def test_valid_token_returns_its_subject():
    assert token_identity.verify_subject_sync(_token(), jwks_client=FakeJwksClient()) == "user-1"


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        _token(key=OTHER_KEY),  # signed by a key the issuer does not publish
        _token(kid="unknown"),
        _token(exp=int(time.time()) - 10),
        _token(iss="https://evil.example.org/"),
        _token(sub=None),
    ],
    ids=["garbage", "wrong-key", "unknown-kid", "expired", "foreign-issuer", "no-sub"],
)
def test_invalid_tokens_have_no_subject(token):
    assert token_identity.verify_subject_sync(token, jwks_client=FakeJwksClient()) is None


def test_unknown_kid_does_not_force_a_jwks_refetch():
    client = FakeJwksClient()
    token_identity.verify_subject_sync(_token(kid="unknown"), jwks_client=client)
    assert client.calls == 1  # the cached set only; get_signing_keys(refresh=True) is never used


def test_jwks_failure_skips_verification_for_a_cooldown():
    broken = FakeJwksClient(error=jwt.PyJWKClientConnectionError("down"))
    assert token_identity.verify_subject_sync(_token(), jwks_client=broken) is None
    assert token_identity.verify_subject_sync(_token(), jwks_client=broken) is None
    assert broken.calls == 1


@pytest.mark.anyio
async def test_async_verifier_rejects_garbage_without_a_thread(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("no thread hop expected")

    monkeypatch.setattr(token_identity.to_thread, "run_sync", fail)
    assert await token_identity.verify_token_subject("not-a-jwt") is None


@pytest.mark.anyio
async def test_async_verifier_returns_the_subject(monkeypatch):
    monkeypatch.setattr(token_identity, "get_jwks_client", lambda: FakeJwksClient())
    assert await token_identity.verify_token_subject(_token()) == "user-1"
