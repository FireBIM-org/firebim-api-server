"""Bearer token verification for request rate limiting (app/core/rate_limit.py).

Checks an Authentik access token's signature, issuer and expiry against the JWKS client that app.auth.jwt caches and
returns its ``sub``, so rate limits follow the identity rather than the token string. Unlike get_current_user it never
touches the database and never forces a JWKS refetch for an unknown ``kid``: a flood of invented tokens costs a
header parse, or at most one signature check, each. If the JWKS cannot be fetched, verification is skipped for a
short cooldown and such requests are limited per client IP; authentication itself still happens in the routes.
"""

from __future__ import annotations

import logging
import time

import jwt
from anyio import to_thread

from app.auth.jwt import get_jwks_client
from app.core.config import settings

logger = logging.getLogger(__name__)

ALGORITHMS = ["RS256", "ES256"]
JWKS_FAILURE_COOLDOWN_SECONDS = 30.0

_jwks_unavailable_until = 0.0


def verify_subject_sync(token: str, *, jwks_client=None) -> str | None:
    """The verified ``sub`` of ``token``, or None when it is malformed, unsigned by a known key, expired or foreign."""
    global _jwks_unavailable_until
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError:
        return None
    if time.monotonic() < _jwks_unavailable_until:
        return None
    try:
        # Served from the client's JWK set cache; only an expired cache triggers a fetch.
        signing_keys = (jwks_client or get_jwks_client()).get_signing_keys()
    except jwt.PyJWTError:
        _jwks_unavailable_until = time.monotonic() + JWKS_FAILURE_COOLDOWN_SECONDS
        logger.warning("JWKS unavailable; rate limiting bearer requests by client IP for a while", exc_info=True)
        return None
    signing_key = next((key for key in signing_keys if key.key_id == kid), None)
    if signing_key is None:
        return None
    try:
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            issuer=settings.AUTHENTIK_ISSUER_URL,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        return None
    subject = payload.get("sub")
    return str(subject) if subject else None


async def verify_token_subject(token: str) -> str | None:
    """Async ``TokenVerifier`` for RateLimitMiddleware; a possible JWKS fetch runs in the threadpool."""
    try:
        jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None  # not even a JWT: no thread hop
    return await to_thread.run_sync(verify_subject_sync, token)


__all__ = ["verify_subject_sync", "verify_token_subject"]
