"""Who is an admin, and the ``require_admin`` dependency for /admin.

A user is an admin when the verified access token's ``groups`` claim contains ADMIN_GROUP, or when the token's email
is listed in ADMIN_EMAILS.

Authentik puts the scope-mapping claims (the default ``profile`` mapping emits ``groups``, the names of the user's
groups) into its JWT access tokens when the provider has ``include_claims_in_id_token`` enabled, as the FireBIM
blueprints do. Group changes therefore take effect when the webapp next refreshes the token. If a token carries no
``groups`` claim at all, the OIDC userinfo endpoint is asked instead, with the same bearer token. Its answer is cached
per sha256(token) for the token's remaining lifetime, five minutes at most, in a bounded LRU; a failed lookup is
cached for 30 seconds and counts as "not in the group".

Authentik reports ``email_verified: false`` for every user and self-enrollment does not verify addresses, so anyone
can sign up with any address. The ADMIN_EMAILS fallback is therefore only honoured while exactly one local user has
that email and that user is the caller; a second account with the same address disables the fallback for both (and
logs a warning). Prefer the group.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.jwt import get_current_active_user
from app.core.config import settings
from app.core.errors import ApiError
from app.models import database
from app.models.models import User

logger = logging.getLogger(__name__)

USERINFO_CACHE_MAX_SECONDS = 300.0
USERINFO_FAILURE_CACHE_SECONDS = 30.0
USERINFO_TIMEOUT_SECONDS = 3.0


class _TtlLru:
    """Bounded ``key -> value`` cache with a per-entry expiry; thread-safe."""

    def __init__(self, *, max_entries: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[bool, Any]:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return False, None
            expires_at, value = entry
            if expires_at <= self._clock():
                del self._items[key]
                return False, None
            self._items.move_to_end(key)
            return True, value

    def put(self, key: str, value: Any, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._items[key] = (self._clock() + ttl_seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


_userinfo_cache = _TtlLru(max_entries=2048)
# Last admin status determined for each user in this process, for AdminUser.is_admin (None = not seen yet).
_known_admin: OrderedDict[str, bool] = OrderedDict()
_known_admin_lock = threading.Lock()
_KNOWN_ADMIN_MAX = 10_000
_userinfo_endpoint: str | None = None


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=USERINFO_TIMEOUT_SECONDS, follow_redirects=False)


def _authentik_base_url() -> str:
    # Like the JWKS client: the internal URL (reachable inside the cluster) if set, otherwise the issuer.
    return (settings.AUTHENTIK_INTERNAL_URL or settings.AUTHENTIK_ISSUER_URL).rstrip("/") + "/"


async def _discover_userinfo_endpoint(client: httpx.AsyncClient) -> str:
    """The provider's ``userinfo_endpoint``, re-based onto the configured (internal) origin.

    Authentik serves userinfo once for all providers at ``/application/o/userinfo/``, not under the per-provider
    issuer path; that layout is the fallback when discovery fails."""
    global _userinfo_endpoint
    if _userinfo_endpoint:
        return _userinfo_endpoint
    base = _authentik_base_url()
    origin = urlsplit(base)
    endpoint: str | None = None
    try:
        response = await client.request("GET", urljoin(base, ".well-known/openid-configuration"))
        response.raise_for_status()
        discovered = response.json().get("userinfo_endpoint")
        if isinstance(discovered, str) and discovered:
            endpoint = f"{origin.scheme}://{origin.netloc}{urlsplit(discovered).path}"
            _userinfo_endpoint = endpoint
    except Exception:
        logger.warning("OIDC discovery failed; using Authentik's default userinfo path", exc_info=True)
    return endpoint or urljoin(base, "../userinfo/")


async def fetch_userinfo(token: str) -> dict[str, Any] | None:
    """The userinfo claims for ``token``, or None when the endpoint cannot be reached or rejects the token."""
    try:
        async with _http_client() as client:
            url = await _discover_userinfo_endpoint(client)
            response = await client.request("GET", url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            payload = response.json()
    except Exception:
        logger.warning("OIDC userinfo lookup failed; treating the user as outside the admin group", exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _token_ttl(claims: dict[str, Any], cap: float) -> float:
    try:
        remaining = float(claims["exp"]) - time.time()
    except (KeyError, TypeError, ValueError):
        return cap
    return max(0.0, min(cap, remaining))


async def userinfo_claims(token: str, claims: dict[str, Any]) -> dict[str, Any] | None:
    """``fetch_userinfo`` cached per sha256(token) for the token's remaining lifetime (5 min at most)."""
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    found, cached = _userinfo_cache.get(key)
    if found:
        return cached
    info = await fetch_userinfo(token)
    cap = USERINFO_CACHE_MAX_SECONDS if info is not None else USERINFO_FAILURE_CACHE_SECONDS
    _userinfo_cache.put(key, info, _token_ttl(claims, cap))
    return info


def _group_names(groups: Any) -> list[str]:
    if isinstance(groups, str):
        return [groups]
    if isinstance(groups, list | tuple | set):
        return [str(group) for group in groups]
    return []


def _user_ids_with_email(email: str) -> list[str]:
    """Ids of the local users whose email is ``email`` (case-insensitive); two at most, enough to spot a duplicate."""
    with Session(database.engine) as session:
        return list(session.execute(select(User.id).where(func.lower(User.email) == email).limit(2)).scalars())


async def _is_admin_email(user: User, email: Any) -> bool:
    if not isinstance(email, str) or not email.strip():
        return False
    email = email.strip().lower()
    if email not in settings.admin_emails:
        return False
    try:
        matches = await run_in_threadpool(_user_ids_with_email, email)
    except Exception:
        logger.warning("Could not check ADMIN_EMAILS uniqueness; not granting admin by email", exc_info=True)
        return False
    if len(matches) > 1:
        logger.warning("Several local users share an ADMIN_EMAILS address; the email fallback is ignored for them")
        return False
    # The address must be the caller's own local email: an email from the userinfo fallback is never synced to the
    # caller's row by get_current_user, so a single match could be someone else.
    return matches == [user.id]


def remember_admin_status(user_id: str, is_admin: bool) -> None:
    with _known_admin_lock:
        _known_admin[user_id] = is_admin
        _known_admin.move_to_end(user_id)
        while len(_known_admin) > _KNOWN_ADMIN_MAX:
            _known_admin.popitem(last=False)


def known_admin_status(user_id: str) -> bool | None:
    """The admin status last determined for ``user_id`` by this process, or None if it has not been seen."""
    with _known_admin_lock:
        return _known_admin.get(user_id)


async def resolve_is_admin(user: User, claims: dict[str, Any], token: str | None) -> bool:
    """Whether ``user`` (authenticated with ``token``, whose verified claims are ``claims``) is an admin."""
    groups = claims.get("groups")
    email = claims.get("email")
    if groups is None and token:
        info = await userinfo_claims(token, claims)
        if info is not None:
            groups = info.get("groups")
            email = email or info.get("email")
    in_group = bool(settings.ADMIN_GROUP) and settings.ADMIN_GROUP in _group_names(groups)
    is_admin = in_group or await _is_admin_email(user, email)
    if is_admin or groups is not None:
        remember_admin_status(user.id, is_admin)
    return is_admin


def _bearer_token(request: Request) -> str | None:
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def get_verified_claims(request: Request, _user: User = Depends(get_current_active_user)) -> dict[str, Any]:
    """The verified access-token claims stashed by get_current_user (empty if there are none)."""
    claims = getattr(request.state, "token_claims", None)
    return dict(claims) if isinstance(claims, dict) else {}


async def get_is_admin(
    request: Request,
    user: User = Depends(get_current_active_user),
    claims: dict[str, Any] = Depends(get_verified_claims),
) -> bool:
    return await resolve_is_admin(user, claims, _bearer_token(request))


async def require_admin(
    user: User = Depends(get_current_active_user),
    is_admin: bool = Depends(get_is_admin),
) -> User:
    """The current user, or 403 unless they are an admin."""
    if not is_admin:
        raise ApiError(403, "Admin privileges required", "FORBIDDEN")
    return user


def reset_admin_caches() -> None:
    """Forget cached userinfo answers, the discovered endpoint and remembered statuses (tests)."""
    global _userinfo_endpoint
    _userinfo_cache.clear()
    _userinfo_endpoint = None
    with _known_admin_lock:
        _known_admin.clear()


__all__ = [
    "fetch_userinfo",
    "get_is_admin",
    "get_verified_claims",
    "known_admin_status",
    "remember_admin_status",
    "require_admin",
    "reset_admin_caches",
    "resolve_is_admin",
    "userinfo_claims",
]
