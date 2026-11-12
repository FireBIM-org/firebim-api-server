import asyncio
import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.s3 import BUCKET_NAME, s3_client
from app.models.database import engine
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# Every dependency check gives up after this many seconds. The checks run concurrently, so /health and /readyz
# answer within about this long even when a dependency hangs.
CHECK_TIMEOUT_SECONDS = 2.5
# How long the Celery check waits for the first worker to answer a ping. The ping returns as soon as one
# worker replies; only "no worker at all" waits the full time.
CELERY_PING_TIMEOUT_SECONDS = 2.0


async def _run_check(func):
    if settings.ENVIRONMENT == "test":
        return func()
    return await asyncio.wait_for(asyncio.to_thread(func), timeout=CHECK_TIMEOUT_SECONDS)


def _check_db_sync() -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


async def _db_check() -> tuple[str, str, bool]:
    try:
        await _run_check(_check_db_sync)
        return "database", "ok", True
    except Exception as e:
        logger.error("Health check database error: %r", e)
        return "database", "error", False


async def _s3_check() -> tuple[str, str, bool]:
    try:
        await _run_check(lambda: s3_client.head_bucket(Bucket=BUCKET_NAME))
        return "s3", "ok", True
    except Exception as e:
        logger.error("Health check S3 error: %r", e)
        return "s3", "error", False


def _ping_celery_workers() -> list:
    # limit=1: stop collecting replies at the first worker instead of waiting out the timeout for more.
    return celery_app.control.ping(timeout=CELERY_PING_TIMEOUT_SECONDS, limit=1)


async def _celery_check() -> tuple[str, str, bool]:
    try:
        result = await _run_check(_ping_celery_workers)
        if result:
            return "celery", "ok", True
        return "celery", "no workers responding", False
    except Exception as e:
        logger.error("Health check Celery error: %r", e)
        return "celery", "error", False


async def _fetch_authentik_discovery() -> None:
    issuer = (settings.AUTHENTIK_INTERNAL_URL or settings.AUTHENTIK_ISSUER_URL).rstrip("/")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{issuer}/.well-known/openid-configuration", timeout=CHECK_TIMEOUT_SECONDS)
        resp.raise_for_status()


async def _authentik_check() -> tuple[str, str, bool]:
    try:
        # httpx applies its timeout per connect/read step; bound the whole request.
        await asyncio.wait_for(_fetch_authentik_discovery(), timeout=CHECK_TIMEOUT_SECONDS)
        return "authentik", "ok", True
    except Exception as e:
        logger.error("Health check Authentik error: %r", e)
        return "authentik", "error", False


async def dependency_checks() -> list[tuple[str, str, bool]]:
    """``(name, status, ok)`` for the database, S3, Celery and Authentik, run concurrently and each bounded by
    ``CHECK_TIMEOUT_SECONDS`` (/health, /admin/system)."""
    return list(await asyncio.gather(_db_check(), _s3_check(), _celery_check(), _authentik_check()))


def _aggregate(results: list[tuple[str, str, bool]] | tuple[tuple[str, str, bool], ...]) -> JSONResponse:
    checks: dict[str, str] = {}
    healthy = True
    for name, check_status, is_ok in results:
        checks[name] = check_status
        if not is_ok:
            healthy = False

    return JSONResponse(
        content={"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=200 if healthy else 503,
    )


@router.get("/livez")
async def livez() -> dict[str, str]:
    """Liveness: answers while the process serves requests. Checks no dependencies."""
    return {"status": "ok"}


@router.get("/readyz", responses={503: {"description": "Service Unavailable"}})
async def readyz() -> JSONResponse:
    """Readiness: the database and object storage are reachable. Authentik and Celery are deliberately excluded,
    so an outage there does not take the API out of rotation."""
    return _aggregate(await asyncio.gather(_db_check(), _s3_check()))


@router.get("/health", responses={503: {"description": "Service Unavailable"}})
async def health() -> JSONResponse:
    return _aggregate(await dependency_checks())
