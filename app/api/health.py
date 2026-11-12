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


async def _run_check(func, *, timeout: float):
    if settings.ENVIRONMENT == "test":
        return func()
    return await asyncio.wait_for(asyncio.to_thread(func), timeout=timeout)


def _check_db_sync() -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


async def _db_check() -> tuple[str, str, bool]:
    try:
        await _run_check(_check_db_sync, timeout=3.0)
        return "database", "ok", True
    except Exception as e:
        logger.error("Health check database error: %s", e)
        return "database", "error", False


async def _s3_check() -> tuple[str, str, bool]:
    try:
        await _run_check(lambda: s3_client.head_bucket(Bucket=BUCKET_NAME), timeout=3.0)
        return "s3", "ok", True
    except Exception as e:
        logger.error("Health check S3 error: %s", e)
        return "s3", "error", False


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

    async def _celery_check() -> tuple[str, str, bool]:
        try:
            result = await _run_check(lambda: celery_app.control.ping(timeout=5), timeout=6.0)
            if result:
                return "celery", "ok", True
            return "celery", "no workers responding", False
        except Exception as e:
            logger.error("Health check Celery error: %s", e)
            return "celery", "error", False

    async def _authentik_check() -> tuple[str, str, bool]:
        try:
            issuer = (settings.AUTHENTIK_INTERNAL_URL or settings.AUTHENTIK_ISSUER_URL).rstrip("/")
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{issuer}/.well-known/openid-configuration", timeout=3.0)
                resp.raise_for_status()
            return "authentik", "ok", True
        except Exception as e:
            logger.error("Health check Authentik error: %s", e)
            return "authentik", "error", False

    results = await asyncio.gather(
        _db_check(),
        _s3_check(),
        _celery_check(),
        _authentik_check(),
    )

    return _aggregate(results)
