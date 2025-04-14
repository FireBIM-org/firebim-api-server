from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends
from fastapi.responses import FileResponse
import uuid
import os
import boto3
from sqlmodel import Session
from typing import Optional
import httpx

from models.database import get_db, init_db
from models.models import IfcModel, IdsFile, User
from auth.router import router as auth_router
from auth.jwt import get_current_active_user

app = FastAPI(title="FireBIM API")

# Include auth router
app.include_router(auth_router)

# Initialize database tables on startup
@app.on_event("startup")
async def startup_event():
    init_db()
    print("Database tables created successfully")

# S3 Configuration
s3_client = boto3.client(
    's3',
    endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://minio:9000"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "firebim")
WORKER_URL = os.getenv("WORKER_SERVICE_URL", "http://worker-service:8000")

# Create bucket if it doesn't exist
try:
    s3_client.head_bucket(Bucket=BUCKET_NAME)
except:
    s3_client.create_bucket(Bucket=BUCKET_NAME)

@app.post("/model", status_code=201)
async def upload_model(
    file: UploadFile = File(...), 
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload an IFC-STEP file, save to S3, and process it with a worker.
    """
    if not file.filename.lower().endswith('.ifc'):
        raise HTTPException(status_code=400, detail="File must be an IFC (STEP) file")
    
    # Generate a unique ID for the model - SQLModel will auto-generate the ID now
    # Read the file content
    content = await file.read()
    
    # Upload to S3
    model_id = str(uuid.uuid4())  # Generate ID for S3 path
    s3_key = f"models/{model_id}/{file.filename}"
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Body=content
    )
    
    # Create DB entry
    model = IfcModel(
        id=model_id,
        filename=file.filename,
        s3_key=s3_key,
        user_id=current_user.id
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    
    # Send to worker for processing in the background
    background_tasks.add_task(send_to_worker, model_id, s3_key)
    
    return {"model_id": model_id}

@app.get("/model/{model_id}")
async def get_model(
    model_id: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Download an IFC-STEP file by its ID.
    """
    # Get model from DB
    model = db.query(IfcModel).filter(IfcModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Check if the model belongs to the current user
    if model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this model")
    
    # Download from S3
    local_path = f"/tmp/{model.filename}"
    s3_client.download_file(
        Bucket=BUCKET_NAME,
        Key=model.s3_key,
        Filename=local_path
    )
    
    return FileResponse(
        path=local_path,
        filename=model.filename,
        media_type="application/octet-stream"
    )

@app.post("/ids", status_code=201)
async def upload_ids(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload an IDS file (XML-based) and save to S3.
    """
    if not file.filename.lower().endswith('.ids'):
        raise HTTPException(status_code=400, detail="File must be an .ids (XML) file")
    
    # Generate a unique ID for the IDS file
    ids_id = str(uuid.uuid4())
    
    # Read the file content
    content = await file.read()
    
    # Upload to S3
    s3_key = f"ids/{ids_id}/{file.filename}"
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Body=content
    )
    
    # Create DB entry
    ids_file = IdsFile(
        id=ids_id,
        filename=file.filename,
        s3_key=s3_key,
        user_id=current_user.id
    )
    db.add(ids_file)
    db.commit()
    db.refresh(ids_file)
    
    return {"ids_id": ids_id}

@app.get("/ids/{ids_id}")
async def get_ids(
    ids_id: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Download an IDS file by its ID.
    """
    # Get IDS file from DB
    ids_file = db.query(IdsFile).filter(IdsFile.id == ids_id).first()
    if not ids_file:
        raise HTTPException(status_code=404, detail="IDS file not found")
    
    # Check if the ids file belongs to the current user
    if ids_file.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this IDS file")
    
    # Download from S3
    local_path = f"/tmp/{ids_file.filename}"
    s3_client.download_file(
        Bucket=BUCKET_NAME,
        Key=ids_file.s3_key,
        Filename=local_path
    )
    
    return FileResponse(
        path=local_path,
        filename=ids_file.filename,
        media_type="application/xml"
    )

@app.get("/ids/{ids_id}/check/{model_id}")
async def check_model_with_ids(
    ids_id: str, 
    model_id: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Send command to a worker to process the model with the IDS file.
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
    
    # Send request to worker
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{WORKER_URL}/check",
                json={
                    "ids_id": ids_id,
                    "model_id": model_id,
                    "ids_s3_key": ids_file.s3_key,
                    "model_s3_key": model.s3_key
                },
                timeout=60.0  # Longer timeout as processing may take time
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")

async def send_to_worker(model_id: str, s3_key: str):
    """
    Send a model to a worker for processing.
    """
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{WORKER_URL}/process",
                json={"model_id": model_id, "s3_key": s3_key},
                timeout=30.0
            )
    except Exception as e:
        print(f"Error sending model to worker: {str(e)}")
        # In a real application, you might want to implement retry logic or error tracking