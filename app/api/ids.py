import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.auth.jwt import get_current_active_user
from app.core.s3 import BUCKET_NAME, s3_client
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


@router.get("/{id}")
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


@router.get("")
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


@router.get("/{id}/download")
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
