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
from app.models.models import IdsFile, User

router = APIRouter(prefix="/ids-files", tags=["ids-files"])


@router.post("", status_code=201)
async def upload_ids(
    file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Upload an IDS file (XML-based) and save to S3.
    """
    filename = display_filename(file.filename)
    if not filename.lower().endswith(".ids"):
        raise HTTPException(status_code=400, detail="File must be an .ids (XML) file")
    ensure_upload_size(file)

    # Generate a unique ID for the IDS file
    id = str(uuid.uuid4())

    # Stream the spooled upload to S3 (never the whole file in memory)
    s3_key = f"ids/{id}/{safe_storage_filename(filename)}"
    await upload_file_to_s3(
        s3_client, bucket=BUCKET_NAME, key=s3_key, file=file, user_id=current_user.id, content_type="application/xml"
    )

    # Create DB entry
    ids_file = IdsFile(id=id, filename=filename, s3_key=s3_key, user_id=current_user.id)
    db.add(ids_file)
    db.commit()
    db.refresh(ids_file)

    return {"id": id}


@router.get("/{id}")
async def get_ids(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get metadata for an IDS file by its ID.
    """
    # Get IDS file from DB
    ids_file = db.exec(select(IdsFile).where(IdsFile.id == id)).first()
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


@router.get("")
async def get_ids_files(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Get all IDS files belonging to the current user.
    """
    ids_files = db.exec(select(IdsFile).where(IdsFile.user_id == current_user.id)).all()

    return [
        {
            "id": ids_file.id,
            "filename": ids_file.filename,
            "created_at": ids_file.created_at,
            "updated_at": ids_file.updated_at,
        }
        for ids_file in ids_files
    ]


@router.get("/{id}/download")
async def download_ids(id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Download an IDS file by its ID.
    """
    # Get IDS file from DB
    ids_file = db.exec(select(IdsFile).where(IdsFile.id == id)).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")

    # Check if the ids file belongs to the current user
    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")

    # Stream from S3
    return await s3_download_response(
        s3_client, bucket=BUCKET_NAME, key=ids_file.s3_key, filename=ids_file.filename, media_type="application/xml"
    )
