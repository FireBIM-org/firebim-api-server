import json
import time
import uuid

import boto3
from fastapi import Depends, FastAPI, File, HTTPException, Query, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from firebim_common.celery import celery_app
from firebim_common.config import settings
from firebim_common.database import get_db, init_db
from firebim_common.models import CheckResult, IdsFile, IfcModel, User
from sqlmodel import Session

from firebim_api.auth.jwt import get_current_active_user
from firebim_api.routes.auth import router as auth_router
from firebim_api.routes.graph import router as graph_router

app = FastAPI(title="FireBIM API")

# Include routers
app.include_router(auth_router)
app.include_router(graph_router)

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


@app.on_event("startup")
async def startup_event():
    init_db()
    print("Database tables created successfully")


# S3 Configuration
s3_client = boto3.client(
    "s3",
    endpoint_url=settings.S3_ENDPOINT_URL,
    aws_access_key_id=settings.S3_ACCESS_KEY_ID,
    aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY.get_secret_value(),
    region_name=settings.S3_REGION,
)

BUCKET_NAME = settings.S3_BUCKET_NAME

# Create bucket if it doesn't exist
try:
    s3_client.head_bucket(Bucket=BUCKET_NAME)
except s3_client.exceptions.NoSuchBucket:
    s3_client.create_bucket(Bucket=BUCKET_NAME)


@app.post("/ifc-models", status_code=201)
async def upload_model(
    file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Upload an IFC-STEP file, save to S3, and queue it for processing.
    """
    if not file.filename.lower().endswith(".ifc"):
        raise HTTPException(status_code=400, detail="File must be an IFC (STEP) file")

    content = await file.read()
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

    model.task_id = task.id
    model.processing_status = "processing"
    db.commit()

    return {"id": id, "task_id": task.id, "status": "processing"}


@app.get("/ifc-models/{id}")
async def get_model(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get metadata for an IFC model by its ID.
    """
    model = db.query(IfcModel).filter(IfcModel.id == id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

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
    model = db.query(IfcModel).filter(IfcModel.id == id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

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

    id = str(uuid.uuid4())
    content = await file.read()

    s3_key = f"ids/{id}/{file.filename}"
    s3_client.put_object(Bucket=BUCKET_NAME, Key=s3_key, Body=content)

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
    ids_file = db.query(IdsFile).filter(IdsFile.id == id).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")

    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")

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
    ids_file = db.query(IdsFile).filter(IdsFile.id == id).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")

    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")

    local_path = f"/tmp/{ids_file.filename}"
    s3_client.download_file(Bucket=BUCKET_NAME, Key=ids_file.s3_key, Filename=local_path)

    return FileResponse(path=local_path, filename=ids_file.filename, media_type="application/xml")


@app.post("/async/check-model-with-ids", status_code=202)
async def async_check_model_with_ids(
    model_id: str, ids_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Asynchronously check a model against an IDS file.
    Returns immediately with a task ID for polling the result.
    """
    ids_file = db.query(IdsFile).filter(IdsFile.id == ids_id).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")

    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")

    model = db.query(IfcModel).filter(IfcModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    check_result = CheckResult(ifc_model_id=model_id, ids_id=ids_id, status="pending")
    db.add(check_result)
    db.commit()
    db.refresh(check_result)

    task = celery_app.send_task("check_model_with_ids", args=[ids_id, model_id, ids_file.s3_key, model.s3_key])

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
    """
    check_result = db.query(CheckResult).filter(CheckResult.id == check_id).first()
    if not check_result:
        raise HTTPException(status_code=404, detail="Check result not found")

    model = db.query(IfcModel).filter(IfcModel.id == check_result.ifc_model_id).first()
    if not model or model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this check result")

    if check_result.status == "completed" and check_result.result_json:
        return JSONResponse(content=json.loads(check_result.result_json))
    elif check_result.status == "failed":
        return {"status": "failed", "message": "Check operation failed"}
    elif check_result.task_id:
        task_result = celery_app.AsyncResult(check_result.task_id)

        if task_result.ready():
            result = task_result.get()

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
    """
    ids_file = db.query(IdsFile).filter(IdsFile.id == ids_id).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")

    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")

    model = db.query(IfcModel).filter(IfcModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    check_result = CheckResult(ifc_model_id=model_id, ids_id=ids_id, status="processing")
    db.add(check_result)
    db.commit()
    db.refresh(check_result)

    task = celery_app.send_task("check_model_with_ids", args=[ids_id, model_id, ids_file.s3_key, model.s3_key])

    check_result.task_id = task.id
    db.commit()

    start_time = time.time()
    while time.time() - start_time < timeout:
        task_result = celery_app.AsyncResult(task.id)
        if task_result.ready():
            result = task_result.get()

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

        time.sleep(1)

    check_result.status = "failed"
    db.commit()
    raise HTTPException(status_code=504, detail="Operation timed out")
