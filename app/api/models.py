import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from app.auth.jwt import get_current_active_user
from app.core.s3 import BUCKET_NAME, s3_client
from app.core.uploads import (
    display_filename,
    ensure_upload_size,
    s3_download_response,
    safe_storage_filename,
    upload_file_to_s3,
)
from app.models.database import get_db
from app.models.models import IfcEntity, IfcModel, IfcProperty, IfcQuantity, User
from app.schemas.models import IfcSqlDataUpload
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/ifc-models", tags=["ifc-models"])


@router.post("", status_code=201)
async def upload_model(
    file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Upload an IFC-STEP file, save to S3, and queue it for processing.
    """
    filename = display_filename(file.filename)
    if not filename.lower().endswith(".ifc"):
        raise HTTPException(status_code=400, detail="File must be an IFC (STEP) file")
    ensure_upload_size(file)

    # Generate a unique ID for the model
    id = str(uuid.uuid4())

    # Stream the spooled upload to S3 (never the whole file in memory)
    s3_key = f"models/{id}/{safe_storage_filename(filename)}"
    await upload_file_to_s3(s3_client, bucket=BUCKET_NAME, key=s3_key, file=file)

    # Create DB entry
    model = IfcModel(id=id, filename=filename, s3_key=s3_key, user_id=current_user.id, processing_status="pending")
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

    # Stream from S3
    return await s3_download_response(
        s3_client, bucket=BUCKET_NAME, key=model.s3_key, filename=model.filename, media_type="application/octet-stream"
    )


@router.post("/{id}/data-payload", status_code=201)
async def upload_data_payload(
    id: str,
    data: IfcSqlDataUpload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Bulk upload IFC model data (entities, properties, relationships) into SQL tables.
    """
    # 1. Verify model exists and belongs to user
    model = db.exec(select(IfcModel).where(IfcModel.id == id)).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    # 2. Insert Data (Entities, Properties, Quantities)
    # Map Express IDs to Global IDs for this model
    id_to_global_id = {ent.id: ent.global_id for ent in data.entities}

    def get_global_id(express_id: str) -> str | None:
        if express_id in id_to_global_id:
            return id_to_global_id[express_id]
        # Check DB for existing entity in the same model
        result = db.exec(
            select(IfcEntity.global_id).where(IfcEntity.id == express_id).where(IfcEntity.model_id == id)
        ).first()
        if result:
            id_to_global_id[express_id] = result
            return result
        return None

    if data.entities:
        for ent in data.entities:
            if ent.parent_id:
                ent.parent_id = get_global_id(ent.parent_id)

        db.add_all([IfcEntity(**ent.model_dump(), model_id=id) for ent in data.entities])

    if data.properties:
        for prop in data.properties:
            mapped_id = get_global_id(prop.entity_id)
            if mapped_id:
                prop.entity_id = mapped_id
            # else: keep original to let DB enforce FK if desired,
            # but note that original is an Express ID which won't match any Global ID PK.

        db.add_all([IfcProperty(**prop.model_dump()) for prop in data.properties])

    if data.quantities:
        for quant in data.quantities:
            mapped_id = get_global_id(quant.entity_id)
            if mapped_id:
                quant.entity_id = mapped_id

        db.add_all([IfcQuantity(**quant.model_dump()) for quant in data.quantities])

    db.commit()

    return {
        "message": "Data uploaded successfully",
        "entity_count": len(data.entities),
        "property_count": len(data.properties),
        "quantity_count": len(data.quantities),
    }


@router.delete("/{id}", status_code=204)
async def delete_model(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Delete an IFC model, its related data in DB, and its files in S3.
    """
    # 1. Get model from DB and verify ownership
    model = db.exec(select(IfcModel).where(IfcModel.id == id)).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this model")

    # 2. Collect all S3 keys to delete
    s3_keys_to_delete = []
    if model.s3_key:
        s3_keys_to_delete.append(model.s3_key)
    if model.result_key:
        s3_keys_to_delete.append(model.result_key)

    # Also collect result_keys from related check_results
    from app.models.models import CheckResult

    check_results = db.exec(select(CheckResult).where(CheckResult.ifc_model_id == id)).all()
    for cr in check_results:
        if cr.result_key:
            s3_keys_to_delete.append(cr.result_key)

    # 3. Synchronous S3 Cleanup (Best Effort)
    for key in s3_keys_to_delete:
        try:
            s3_client.delete_object(Bucket=BUCKET_NAME, Key=key)
        except Exception as e:
            # Best effort: log and continue
            print(f"Failed to delete S3 object {key}: {e}")

    # 4. DB Deletion (Cascades to IfcEntity, IfcProperty, IfcQuantity, CheckResult)
    db.delete(model)
    db.commit()

    return None
