import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func
from sqlmodel import Session, select

from app.auth.jwt import get_current_active_user
from app.core.config import settings
from app.core.s3 import BUCKET_NAME, s3_client, s3_upload_client
from app.ingestion.admission import IngestionAdmission
from app.ingestion.jobs import AdmissionRuntime
from app.models.database import get_db
from app.models.models import IfcEntity, IfcModel, IfcProperty, IfcQuantity, User
from app.schemas.models import (
    IfcSqlDataUpload,
    IfcUploadComplete,
    IfcUploadInitiate,
    IfcUploadResponse,
    IngestionJobResponse,
    IngestionReportResponse,
    IngestionReprocessRequest,
    IngestionRetryRequest,
    IngestionRevisionsResponse,
)
from app.storage.ingestion_objects import IngestionObjects
from app.storage.translation_profile_repository import TranslationProfileRepository
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/ifc-models", tags=["ifc-models"])


def get_ingestion_admission(db: Session = Depends(get_db)) -> IngestionAdmission:
    return IngestionAdmission(
        db,
        TranslationProfileRepository(s3_client=s3_client),
        IngestionObjects(s3_client, BUCKET_NAME, signing_client=s3_upload_client),
        AdmissionRuntime(
            enabled=settings.NEO_INGESTION_ENABLED,
            max_input_bytes=settings.NEO_INGESTION_MAX_INPUT_BYTES,
            upload_ttl_seconds=settings.NEO_UPLOAD_TTL_SECONDS,
        ),
    )


@router.get("/ingestion-revisions", response_model=IngestionRevisionsResponse)
def ingestion_revisions(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.supported_revisions(current_user, offset, limit)


@router.post("/uploads", status_code=201, response_model=IfcUploadResponse)
def initiate_upload(
    data: IfcUploadInitiate,
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.initiate(data, current_user)


@router.get("/uploads/{upload_id}", response_model=IfcUploadResponse)
def upload_session(
    upload_id: str,
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.upload_status(upload_id, current_user)


@router.post("/uploads/{upload_id}/complete", status_code=202, response_model=IngestionJobResponse)
def complete_upload(
    upload_id: str,
    data: IfcUploadComplete,
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.complete(upload_id, current_user)


@router.get("/jobs/{job_id}", response_model=IngestionJobResponse)
def ingestion_job(
    job_id: str,
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.get_job(job_id, current_user)


@router.get("/jobs/{job_id}/report", response_model=IngestionReportResponse)
def ingestion_report(
    job_id: str,
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.report(job_id, current_user)


@router.post("/jobs/{job_id}/retry", status_code=202, response_model=IngestionJobResponse)
def retry_ingestion(
    job_id: str,
    data: IngestionRetryRequest,
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.retry(job_id, data, current_user)


@router.post("/jobs/{job_id}/cancel", response_model=IngestionJobResponse)
def cancel_ingestion(
    job_id: str,
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.cancel(job_id, current_user)


@router.get("/{id}/jobs", response_model=list[IngestionJobResponse])
def model_jobs(
    id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.list_jobs(id, current_user, offset, limit)


@router.post("/{id}/reprocess", status_code=202, response_model=IngestionJobResponse)
def reprocess_model(
    id: str,
    data: IngestionReprocessRequest,
    service: IngestionAdmission = Depends(get_ingestion_admission),
    current_user: User = Depends(get_current_active_user),
):
    return service.reprocess(id, data, current_user)


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
    if not model or model.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if the model belongs to the current user
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    model_entities = select(IfcEntity.global_id).where(IfcEntity.model_id == id)
    node_count = db.exec(select(func.count()).select_from(IfcEntity).where(IfcEntity.model_id == id)).one()
    edge_count = db.exec(
        select(func.count())
        .select_from(IfcEntity)
        .where(IfcEntity.model_id == id, IfcEntity.parent_id.in_(model_entities))
    ).one()

    # Return metadata
    return {
        "id": model.id,
        "filename": model.filename,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
        "status": model.processing_status,
        "task_id": model.task_id,
        "node_count": node_count,
        "edge_count": edge_count,
        "conversion_profile_id": model.conversion_profile_id,
        "conversion_profile_name": model.conversion_profile_name,
        "ingestion_backend": model.ingestion_backend,
        "latest_job_id": model.latest_job_id,
        "latest_successful_job_id": model.latest_successful_job_id,
        "latest_successful_graph_id": model.latest_successful_graph_id,
        "latest_successful_revision_id": model.latest_successful_revision_id,
    }


@router.get("")
async def get_models(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get all IFC models belonging to the current user.
    """
    models = db.exec(select(IfcModel).where(IfcModel.user_id == current_user.id, IfcModel.deleted_at.is_(None))).all()

    return [
        {
            "id": model.id,
            "filename": model.filename,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "status": model.processing_status,
            "ingestion_backend": model.ingestion_backend,
            "latest_job_id": model.latest_job_id,
            "latest_successful_graph_id": model.latest_successful_graph_id,
        }
        for model in models
    ]


@router.get("/{id}/download")
async def download_model(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    service: IngestionAdmission = Depends(get_ingestion_admission),
):
    """
    Download an IFC-STEP file by its ID.
    """
    # Get model from DB
    model = db.exec(select(IfcModel).where(IfcModel.id == id)).first()
    if not model or model.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if the model belongs to the current user
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")

    if model.ingestion_backend == "neo":
        return Response(
            content=service.raw_input(id, current_user),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(model.filename)}",
                "Cache-Control": "private, no-store",
            },
        )

    # Download from S3
    local_path = f"/tmp/{model.filename}"
    s3_client.download_file(Bucket=BUCKET_NAME, Key=model.s3_key, Filename=local_path)

    return FileResponse(path=local_path, filename=model.filename, media_type="application/octet-stream")


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
    if not model or model.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Model not found")
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")
    if model.ingestion_backend == "neo":
        raise HTTPException(status_code=409, detail="Neo ingestion does not accept browser-extracted SQL payloads")

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

    if data.conversion_profile_id is not None or data.conversion_profile_name is not None:
        model.conversion_profile_id = data.conversion_profile_id
        model.conversion_profile_name = data.conversion_profile_name
        db.add(model)

    db.commit()

    return {
        "message": "Data uploaded successfully",
        "entity_count": len(data.entities),
        "property_count": len(data.properties),
        "quantity_count": len(data.quantities),
    }


@router.delete("/{id}", status_code=204)
async def delete_model(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    service: IngestionAdmission = Depends(get_ingestion_admission),
):
    """
    Delete an IFC model, its related data in DB, and its files in S3.
    """
    # 1. Get model from DB and verify ownership
    model = db.exec(select(IfcModel).where(IfcModel.id == id)).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this model")

    if model.ingestion_backend == "neo":
        service.delete_model(id, current_user)
        return None

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
