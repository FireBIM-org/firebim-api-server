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
from app.api.lexicon import router as lexicon_router
from app.api.models import router as models_router
from app.api.ontologies import router as ontologies_router
from app.api.rulebuilder import router as rulebuilder_router
from app.api.rules import router as rules_router
from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.s3 import BUCKET_NAME, s3_client
from app.models.database import init_db
from app.storage.demo_content_bootstrap import bootstrap_default_demo_content
from app.storage.ontology_bootstrap import bootstrap_default_ontologies
from app.storage.ontology_repository import OntologyRepository

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

    if settings.ENVIRONMENT == "local":
        seeded_ontologies = bootstrap_default_ontologies(ontologies_repo)
        logger.info("Ontology bootstrap completed", extra={"seeded_ontologies": seeded_ontologies})
        if settings.SEED_DEMO_CONTENT:
            seeded_demo_content = bootstrap_default_demo_content()
            logger.info("Demo content bootstrap completed", extra={"seeded_demo_content": seeded_demo_content})
        else:
            logger.info("Demo content bootstrap skipped (SEED_DEMO_CONTENT=false)")

    yield


app = FastAPI(title="FireBIM API", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(build_info_router)
app.include_router(graph_router)
app.include_router(models_router)
app.include_router(ids_router)
app.include_router(lexicon_router)
app.include_router(ontologies_router)
app.include_router(rulebuilder_router)
app.include_router(rules_router)
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
