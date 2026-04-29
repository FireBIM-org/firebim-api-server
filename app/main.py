import asyncio
import json
import logging
import time
import uuid

import boto3
import httpx
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlmodel import Session

from app.api.checks import router as checks_router
from app.api.graphs import router as graphs_router
from app.api.lexicon import router as lexicon_router
from app.api.ontologies import router as ontologies_router
from app.api.rulebuilder import router as rulebuilder_router
from app.api.rules import router as rules_router
from app.auth.jwt import get_current_active_user
from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.observability import log_event, reset_request_context, set_request_context, snapshot_metrics
from app.models.database import engine, get_db, init_db
from app.models.models import CheckResult, IdsFile, IfcModel, User
from app.storage.demo_content_bootstrap import bootstrap_default_demo_content
from app.storage.ontology_bootstrap import bootstrap_default_ontologies
from app.storage.ontology_repository import OntologyRepository
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
ontologies_repo = OntologyRepository()

app = FastAPI(title="FireBIM API")

# Include auth router
app.include_router(auth_router)
app.include_router(graphs_router)
app.include_router(lexicon_router)
app.include_router(ontologies_router)
app.include_router(rulebuilder_router)
app.include_router(rules_router)
app.include_router(checks_router)

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


# Initialize database tables on startup
@app.on_event("startup")
async def startup_event():
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

    seeded_ontologies = bootstrap_default_ontologies(ontologies_repo)
    logger.info("Ontology bootstrap completed", extra={"seeded_ontologies": seeded_ontologies})

    if settings.ENVIRONMENT == "local":
        seeded_demo_content = bootstrap_default_demo_content()
        logger.info("Demo content bootstrap completed", extra={"seeded_demo_content": seeded_demo_content})


# S3 Configuration
s3_client = boto3.client(
    "s3",
    endpoint_url=settings.S3_ENDPOINT_URL,
    aws_access_key_id=settings.S3_ACCESS_KEY_ID,
    aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY.get_secret_value(),
    region_name=settings.S3_REGION,
)

BUCKET_NAME = settings.S3_BUCKET_NAME


@app.get("/health", tags=["health"], responses={503: {"description": "Service Unavailable"}})
async def health() -> JSONResponse:
    async def _db_check() -> tuple[str, str, bool]:
        try:

            def _check_db_sync() -> None:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))

            await asyncio.wait_for(asyncio.to_thread(_check_db_sync), timeout=3.0)
            return "database", "ok", True
        except Exception as e:
            logger.error("Health check database error: %s", e)
            return "database", "error", False

    async def _s3_check() -> tuple[str, str, bool]:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(s3_client.head_bucket, Bucket=BUCKET_NAME),
                timeout=3.0,
            )
            return "s3", "ok", True
        except Exception as e:
            logger.error("Health check S3 error: %s", e)
            return "s3", "error", False

    async def _celery_check() -> tuple[str, str, bool]:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(celery_app.control.ping, timeout=5),
                timeout=6.0,
            )
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


@app.post("/ifc-models", status_code=201)
async def upload_model(
    file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Upload an IFC-STEP file, save to S3, and queue it for processing.
    """
    if not file.filename.lower().endswith(".ifc"):
        raise HTTPException(status_code=400, detail="File must be an IFC (STEP) file")

    # Read the file content
    content = await file.read()

    # Generate a unique ID for the model
    id = str(uuid.uuid4())

    # Upload to S3
    s3_key = f"models/{id}/{file.filename}"
    s3_client.put_object(Bucket=BUCKET_NAME, Key=s3_key, Body=content)

    # Create DB entry
    model = IfcModel(id=id, filename=file.filename, s3_key=s3_key, user_id=current_user.id, processing_status="pending")
    db.add(model)
    db.commit()
    db.refresh(model)

    # Queue task to process the model
    task = celery_app.send_task("process_model", args=[id, s3_key])

    # Update model with task ID
    model.task_id = task.id
    model.processing_status = "processing"
    db.commit()

    return {"id": id, "task_id": task.id, "status": "processing"}


@app.get("/ifc-models/{id}")
async def get_model(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get metadata for an IFC model by its ID.
    """
    # Get model from DB
    model = db.query(IfcModel).filter(IfcModel.id == id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if the model belongs to the current user
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    # Return metadata
    return {
        "id": model.id,
        "filename": model.filename,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
        "status": model.processing_status,
        "task_id": model.task_id,
    }


@app.get("/ifc-models")
async def get_models(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get all IFC models belonging to the current user.
    """
    models = db.query(IfcModel).filter(IfcModel.user_id == current_user.id).all()

    return [
        {
            "id": model.id,
            "filename": model.filename,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "status": model.processing_status,
        }
        for model in models
    ]


@app.get("/ifc-models/{id}/download")
async def download_model(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Download an IFC-STEP file by its ID.
    """
    # Get model from DB
    model = db.query(IfcModel).filter(IfcModel.id == id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if the model belongs to the current user
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    # Download from S3
    local_path = f"/tmp/{model.filename}"
    s3_client.download_file(Bucket=BUCKET_NAME, Key=model.s3_key, Filename=local_path)

    return FileResponse(path=local_path, filename=model.filename, media_type="application/octet-stream")


@app.post("/ids-files", status_code=201)
async def upload_ids(
    file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Upload an IDS file (XML-based) and save to S3.
    """
    if not file.filename.lower().endswith(".ids"):
        raise HTTPException(status_code=400, detail="File must be an .ids (XML) file")

    # Generate a unique ID for the IDS file
    id = str(uuid.uuid4())

    # Read the file content
    content = await file.read()

    # Upload to S3
    s3_key = f"ids/{id}/{file.filename}"
    s3_client.put_object(Bucket=BUCKET_NAME, Key=s3_key, Body=content)

    # Create DB entry
    ids_file = IdsFile(id=id, filename=file.filename, s3_key=s3_key, user_id=current_user.id)
    db.add(ids_file)
    db.commit()
    db.refresh(ids_file)

    return {"id": id}


@app.get("/ids-files/{id}")
async def get_ids(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get metadata for an IDS file by its ID.
    """
    # Get IDS file from DB
    ids_file = db.query(IdsFile).filter(IdsFile.id == id).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")

    # Check if the ids file belongs to the current user
    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")

    # Return metadata
    return {
        "id": ids_file.id,
        "filename": ids_file.filename,
        "created_at": ids_file.created_at,
        "updated_at": ids_file.updated_at,
    }


@app.get("/ids-files")
async def get_ids_files(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get all IDS files belonging to the current user.
    """
    ids_files = db.query(IdsFile).filter(IdsFile.user_id == current_user.id).all()

    return [
        {
            "id": ids_file.id,
            "filename": ids_file.filename,
            "created_at": ids_file.created_at,
            "updated_at": ids_file.updated_at,
        }
        for ids_file in ids_files
    ]


@app.get("/ids-files/{id}/download")
async def download_ids(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Download an IDS file by its ID.
    """
    # Get IDS file from DB
    ids_file = db.query(IdsFile).filter(IdsFile.id == id).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")

    # Check if the ids file belongs to the current user
    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")

    # Download from S3
    local_path = f"/tmp/{ids_file.filename}"
    s3_client.download_file(Bucket=BUCKET_NAME, Key=ids_file.s3_key, Filename=local_path)

    return FileResponse(path=local_path, filename=ids_file.filename, media_type="application/xml")


# New endpoints that implement the required functionality


@app.post("/async/check-model-with-ids", status_code=202)
async def async_check_model_with_ids(
    model_id: str, ids_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Asynchronously check a model against an IDS file.
    Returns immediately with a task ID for polling the result.

    This endpoint demonstrates the first requested pattern:
    "Call that returns immediately with id for polling if result is ready. Task is sent to worker."
    """
    # Validate that both IDS and Model exist
    ids_file = db.query(IdsFile).filter(IdsFile.id == ids_id).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")

    # Check if the ids file belongs to the current user
    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")

    model = db.query(IfcModel).filter(IfcModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if the model belongs to the current user
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    # Create a check result record
    check_result = CheckResult(ifc_model_id=model_id, ids_id=ids_id, status="pending")
    db.add(check_result)
    db.commit()
    db.refresh(check_result)

    # Send task to Celery worker
    task = celery_app.send_task("check_model_with_ids", args=[ids_id, model_id, ids_file.s3_key, model.s3_key])

    # Update check result with task ID
    check_result.task_id = task.id
    check_result.status = "processing"
    db.commit()

    return {
        "check_id": check_result.id,
        "task_id": task.id,
        "status": "processing",
        "message": "Task has been queued. Use the check_id to poll for results.",
    }


@app.get("/async/check-result/{check_id}")
async def get_async_check_result(
    check_id: str,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get the result of an asynchronous check operation by its ID.
    This is the polling endpoint to check if a result is ready.
    """
    # Get check result from DB
    check_result = db.query(CheckResult).filter(CheckResult.id == check_id).first()
    if not check_result:
        raise HTTPException(status_code=404, detail="Check result not found")

    # Verify ownership through model and IDS file
    model = db.query(IfcModel).filter(IfcModel.id == check_result.ifc_model_id).first()
    if not model or model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this check result")

    # Check result status
    if check_result.status == "completed" and check_result.result_json:
        # Return the stored result
        return JSONResponse(content=json.loads(check_result.result_json))
    elif check_result.status == "failed":
        return {"status": "failed", "message": "Check operation failed"}
    elif check_result.task_id:
        # Check task status with Celery
        task_result = celery_app.AsyncResult(check_result.task_id)

        if task_result.ready():
            result = task_result.get()

            # Update the database record
            if result.get("status") == "success":
                check_result.status = "completed"
                check_result.result_json = json.dumps(result.get("check_result", {}))
                check_result.result_key = result.get("result_key")
                db.commit()
                return result.get("check_result", {})
            else:
                check_result.status = "failed"
                db.commit()
                return {"status": "failed", "message": "Check operation failed"}

        response.status_code = status.HTTP_202_ACCEPTED
        return {"status": "processing", "check_id": check_id, "message": "Still processing. Please check again later."}

    return {"status": "unknown", "message": "Could not determine task status"}


# @app.post("/sync/check-model-with-ids")
@app.get("/ifc-models/{model_id}/check")
async def sync_check_model_with_ids(
    model_id: str,
    ids_id: str,
    timeout: int = Query(300, description="Timeout in seconds"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Synchronously check a model against an IDS file.
    Waits for the worker to complete and returns the result.

    This endpoint demonstrates the second requested pattern:
    "Call that waits for the worker to return with the result."
    """
    # Validate that both IDS and Model exist
    ids_file = db.query(IdsFile).filter(IdsFile.id == ids_id).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")

    # Check if the ids file belongs to the current user
    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")

    model = db.query(IfcModel).filter(IfcModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if the model belongs to the current user
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    # Create a check result record
    check_result = CheckResult(ifc_model_id=model_id, ids_id=ids_id, status="processing")
    db.add(check_result)
    db.commit()
    db.refresh(check_result)

    # Send task to Celery worker
    task = celery_app.send_task("check_model_with_ids", args=[ids_id, model_id, ids_file.s3_key, model.s3_key])

    # Update check result with task ID
    check_result.task_id = task.id
    db.commit()

    # Wait for the result with timeout
    start_time = time.time()
    while time.time() - start_time < timeout:
        # Check if task is complete
        task_result = celery_app.AsyncResult(task.id)
        if task_result.ready():
            result = task_result.get()

            # Update the database record
            if result.get("status") == "success":
                check_result.status = "completed"
                check_result.result_json = json.dumps(result.get("check_result", {}))
                check_result.result_key = result.get("result_key")
                db.commit()
                return result.get("check_result", {})
            else:
                check_result.status = "failed"
                db.commit()
                raise HTTPException(status_code=500, detail="Check operation failed")

        # Wait a bit before checking again
        time.sleep(1)

    # If we get here, we've timed out
    check_result.status = "failed"
    db.commit()
    raise HTTPException(status_code=504, detail="Operation timed out")
