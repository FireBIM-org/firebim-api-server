import logging
from time import monotonic

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlmodel import Session, select

from app.auth.activity import record_user_activity
from app.core.config import settings
from app.core.observability import is_request_profiling_enabled, log_event, record_metric, start_db_checkout_wait
from app.models.database import get_auth_db
from app.models.models import User

# Bearer token extraction
security = HTTPBearer()
logger = logging.getLogger(__name__)

# Cached JWKS client for verifying Authentik-signed tokens
_jwks_client = None


def get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        # Use the internal URL (reachable inside Docker) if set, otherwise the public issuer URL
        base_url = settings.AUTHENTIK_INTERNAL_URL or settings.AUTHENTIK_ISSUER_URL
        jwks_url = f"{base_url.rstrip('/')}/jwks/"
        _jwks_client = PyJWKClient(jwks_url)
    return _jwks_client


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_auth_db),
) -> User:
    """Validate an Authentik-issued JWT and return the local user (auto-provisioned)."""
    auth_started_at = monotonic()
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = credentials.credentials

    try:
        jwt_started_at = monotonic()
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            issuer=settings.AUTHENTIK_ISSUER_URL,
            options={"verify_aud": False},
        )

        sub: str = payload.get("sub")
        if sub is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception from None
    finally:
        jwt_duration_ms = round((monotonic() - jwt_started_at) * 1000, 3)
        record_metric("auth.jwt_decode_ms", jwt_duration_ms)
        if is_request_profiling_enabled():
            log_event(logger, "auth.jwt.decoded", duration_ms=jwt_duration_ms)

    # The verified claims (e.g. ``groups``) for app.auth.admin; the return type stays a plain User.
    request.state.token_claims = payload

    # Find or auto-provision user based on Authentik subject
    lookup_started_at = monotonic()
    start_db_checkout_wait("auth_user_lookup")
    user = db.exec(select(User).where(User.authentik_sub == sub)).first()
    lookup_duration_ms = round((monotonic() - lookup_started_at) * 1000, 3)
    record_metric("auth.user_lookup_ms", lookup_duration_ms)
    if is_request_profiling_enabled():
        log_event(logger, "auth.user.lookup", duration_ms=lookup_duration_ms, found=user is not None)

    if user is None:
        email = payload.get("email", "")
        username = payload.get("preferred_username", sub)

        user = User(
            authentik_sub=sub,
            username=username,
            email=email,
            is_active=True,
        )
        provision_started_at = monotonic()
        start_db_checkout_wait("auth_user_provision_commit")
        db.add(user)
        db.commit()
        start_db_checkout_wait("auth_user_provision_refresh")
        db.refresh(user)
        provision_duration_ms = round((monotonic() - provision_started_at) * 1000, 3)
        record_metric("auth.user_provision_ms", provision_duration_ms)
        if is_request_profiling_enabled():
            log_event(logger, "auth.user.provisioned", duration_ms=provision_duration_ms, user_id=user.id)
    else:
        # Keep local profile in sync with Authentik claims
        updated = False
        email = payload.get("email")
        username = payload.get("preferred_username")

        if email and user.email != email:
            user.email = email
            updated = True
        if username and user.username != username:
            user.username = username
            updated = True
        if updated:
            sync_started_at = monotonic()
            start_db_checkout_wait("auth_user_sync_commit")
            db.commit()
            start_db_checkout_wait("auth_user_sync_refresh")
            db.refresh(user)
            sync_duration_ms = round((monotonic() - sync_started_at) * 1000, 3)
            record_metric("auth.user_sync_ms", sync_duration_ms)
            if is_request_profiling_enabled():
                log_event(logger, "auth.user.synced", duration_ms=sync_duration_ms, user_id=user.id)

    db.expunge(user)
    db.close()
    if is_request_profiling_enabled():
        log_event(logger, "auth.db_session.released")
    total_auth_ms = round((monotonic() - auth_started_at) * 1000, 3)
    record_metric("auth.total_ms", total_auth_ms)
    if is_request_profiling_enabled():
        log_event(logger, "auth.completed", duration_ms=total_auth_ms, user_id=user.id)
    await record_user_activity(user.id)
    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)):
    """Check if the current user is active."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user
