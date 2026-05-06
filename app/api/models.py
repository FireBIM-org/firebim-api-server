import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from app.auth.jwt import get_current_active_user
from app.core.s3 import BUCKET_NAME, s3_client
from app.models.database import get_db
from app.models.models import IfcModel, User
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/ifc-models", tags=["ifc-models"])


@router.post("", status_code=201)
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


@router.get("/{id}")
async def get_model(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get metadata for an IFC model by its ID.
    """
    # Get model from DB
    model = db.exec(select(IfcModel).where(IfcModel.id == id)).first()
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


@router.get("")
async def get_models(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get all IFC models belonging to the current user.
    """
    models = db.exec(select(IfcModel).where(IfcModel.user_id == current_user.id)).all()

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


@router.get("/{id}/download")
async def download_model(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Download an IFC-STEP file by its ID.
    """
    # Get model from DB
    model = db.exec(select(IfcModel).where(IfcModel.id == id)).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if the model belongs to the current user
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    # Download from S3
    local_path = f"/tmp/{model.filename}"
    s3_client.download_file(Bucket=BUCKET_NAME, Key=model.s3_key, Filename=local_path)

    return FileResponse(path=local_path, filename=model.filename, media_type="application/octet-stream")
