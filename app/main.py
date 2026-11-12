import logging
import time
import uuid
from contextlib import asynccontextmanager

from botocore.exceptions import ClientError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.build_info import router as build_info_router
from app.api.checks import public_router as checks_public_router
from app.api.checks import router as checks_router
from app.api.graphs import public_router as graph_public_router
from app.api.graphs import router as graph_router
from app.api.health import router as health_router
from app.api.ids import router as ids_router
from app.api.lexicon import router as lexicon_router
from app.api.models import router as models_router
from app.api.ontologies import router as ontologies_router
from app.api.rulebuilder import router as rulebuilder_router
from app.api.rules import public_router as rules_public_router
from app.api.rules import router as rules_router
from app.auth.router import router as auth_router
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.observability import log_event, reset_request_context, set_request_context, snapshot_metrics
from app.core.rate_limit import RateLimitMiddleware
from app.core.s3 import BUCKET_NAME, s3_client
from app.core.security_headers import SecurityHeadersMiddleware
from app.models.database import init_db
from app.storage.demo_content_bootstrap import bootstrap_default_demo_content
from app.storage.ontology_bootstrap import bootstrap_default_ontologies
from app.storage.ontology_repository import OntologyRepository
from app.storage.reconcile import start_startup_reconcile

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
ontologies_repo = OntologyRepository()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database tables created successfully")
    # run_migrations()

    try:
        s3_client.head_bucket(Bucket=BUCKET_NAME)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "404":
            if settings.S3_ENDPOINT_URL:
                s3_client.create_bucket(Bucket=BUCKET_NAME)
            else:
                raise RuntimeError(
                    f"S3 bucket '{BUCKET_NAME}' does not exist. "
                    "When using external S3, buckets must be pre-provisioned."
                ) from exc
        else:
            raise

    if settings.STORAGE_RECONCILE_ON_STARTUP:
        # Best effort and non-blocking; a pg advisory lock keeps replicas from listing the bucket twice.
        start_startup_reconcile()

    if settings.ENVIRONMENT == "local":
        seeded_ontologies = bootstrap_default_ontologies(ontologies_repo)
        logger.info("Ontology bootstrap completed", extra={"seeded_ontologies": seeded_ontologies})
        if settings.SEED_DEMO_CONTENT:
            seeded_demo_content = bootstrap_default_demo_content()
            logger.info("Demo content bootstrap completed", extra={"seeded_demo_content": seeded_demo_content})
        else:
            logger.info("Demo content bootstrap skipped (SEED_DEMO_CONTENT=false)")

    yield


def api_docs_urls(enabled: bool) -> dict[str, str | None]:
    """FastAPI docs/schema URLs; all None when ENABLE_API_DOCS is off."""
    if not enabled:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(title="FireBIM API", lifespan=lifespan, **api_docs_urls(settings.ENABLE_API_DOCS))
install_error_handlers(app)

app.include_router(auth_router)
app.include_router(build_info_router)
app.include_router(graph_public_router)
app.include_router(graph_router)
app.include_router(models_router)
app.include_router(ids_router)
app.include_router(lexicon_router)
app.include_router(ontologies_router)
app.include_router(rulebuilder_router)
app.include_router(rules_public_router)
app.include_router(rules_router)
app.include_router(checks_public_router)
app.include_router(checks_router)
app.include_router(health_router)

origins = settings.all_cors_origins

if settings.ENVIRONMENT == "local":
    origins.extend(
        [
            "http://localhost",
            "https://localhost",
            "http://localhost:80",
            "https://localhost:443",
            "http://localhost:3000",
            "https://localhost:3000",
        ]
    )

logger.info("CORS origins configured", extra={"origins": origins})

# Middleware added later wraps the ones added earlier. Resulting order, outermost first:
# security headers -> request id/observability -> CORS -> rate limit -> body size limit -> routes.
# Rate limiting sits inside CORS so browsers can read 429 responses; the body limit only runs for admitted requests.
app.add_middleware(
    BodySizeLimitMiddleware,
    max_json_body_bytes=settings.MAX_JSON_BODY_BYTES,
    max_upload_bytes=settings.MAX_UPLOAD_BYTES,
)
app.add_middleware(
    RateLimitMiddleware,
    enabled=settings.RATE_LIMIT_ENABLED,
    default=settings.RATE_LIMIT_DEFAULT,
    expensive=settings.RATE_LIMIT_EXPENSIVE,
    anon=settings.RATE_LIMIT_ANON,
    trusted_proxy_cidrs=settings.trusted_proxy_cidrs,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_observability_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    profiling_enabled = request.headers.get("x-firebim-profile") == "1" or request.url.path.startswith(
        "/lexicon/profiles"
    )
    tokens = set_request_context(
        request_id=request_id,
        endpoint=request.url.path,
        method=request.method,
        profiling_enabled=profiling_enabled,
    )
    request.state.request_id = request_id
    request.state.request_started_at = time.monotonic()
    request.state.handler_completed_at = None

    if profiling_enabled:
        log_event(logger, "request.started")

    try:
        response = await call_next(request)
    finally:
        total_ms = round((time.monotonic() - request.state.request_started_at) * 1000, 3)
        handler_completed_at = getattr(request.state, "handler_completed_at", None)
        handler_ms = (
            round((handler_completed_at - request.state.request_started_at) * 1000, 3)
            if handler_completed_at is not None
            else None
        )
        response_ms = round(total_ms - handler_ms, 3) if handler_ms is not None else None
        metrics = snapshot_metrics()
        if profiling_enabled:
            log_event(
                logger,
                "request.completed",
                status_code=getattr(locals().get("response"), "status_code", None),
                total_ms=total_ms,
                handler_ms=handler_ms,
                response_serialization_ms=response_ms,
                metrics=metrics,
            )
        reset_request_context(tokens)

    response.headers["X-Request-ID"] = request_id
    return response


app.add_middleware(SecurityHeadersMiddleware)
