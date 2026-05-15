import logging
from contextlib import asynccontextmanager

from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.build_info import router as build_info_router
from app.api.checks import router as checks_router
from app.api.graphs import router as graph_router
from app.api.health import router as health_router
from app.api.ids import router as ids_router
from app.api.models import router as models_router
from app.api.rules import router as rules_router
from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.s3 import BUCKET_NAME, s3_client
from app.models.database import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    init_db()
    print("Database tables created successfully")

    try:
        s3_client.head_bucket(Bucket=BUCKET_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            if settings.S3_ENDPOINT_URL:
                # Only auto-create buckets for S3-compatible services (MinIO)
                s3_client.create_bucket(Bucket=BUCKET_NAME)
            else:
                raise RuntimeError(
                    f"S3 bucket '{BUCKET_NAME}' does not exist. "
                    "When using external S3, buckets must be pre-provisioned."
                ) from e
        else:
            raise
    yield


app = FastAPI(title="FireBIM API", lifespan=lifespan)

# Include auth router
app.include_router(auth_router)
app.include_router(build_info_router)
app.include_router(graph_router)
app.include_router(models_router)
app.include_router(ids_router)
app.include_router(rules_router)
app.include_router(checks_router)
app.include_router(health_router)

# CORS settings
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

print("CORS origins:", origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
