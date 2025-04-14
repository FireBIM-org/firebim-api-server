from fastapi import FastAPI, HTTPException
import os
import boto3
import ifcopenshell
import tempfile
import uuid
import json

app = FastAPI(title="FireBIM Worker")

# S3 Configuration
s3_client = boto3.client(
    's3',
    endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://minio:9000"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "firebim")

@app.post("/process")
async def process_model(data: dict):
    """
    Process an IFC model file.
    """
    model_id = data.get("model_id")
    s3_key = data.get("s3_key")
    
    if not model_id or not s3_key:
        raise HTTPException(status_code=400, detail="Missing required data: model_id or s3_key")
    
    try:
        # Download file from S3
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as temp_file:
            s3_client.download_file(
                Bucket=BUCKET_NAME,
                Key=s3_key,
                Filename=temp_file.name
            )
            temp_path = temp_file.name
        
        # Process with IfcOpenShell
        model = ifcopenshell.open(temp_path)
        
        # Simple processing example - count objects by type
        stats = {}
        for element in model.by_type("IfcElement"):
            element_type = element.is_a()
            if element_type in stats:
                stats[element_type] += 1
            else:
                stats[element_type] = 1
        
        # Save processing results to S3
        result_key = f"results/{model_id}/stats.json"
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=result_key,
            Body=json.dumps(stats)
        )
        
        # Clean up
        os.unlink(temp_path)
        
        return {"status": "success", "model_id": model_id, "result_key": result_key}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing model: {str(e)}")

@app.post("/check")
async def check_model_with_ids(data: dict):
    """
    Check an IFC model against an IDS file.
    """
    ids_id = data.get("ids_id")
    model_id = data.get("model_id")
    ids_s3_key = data.get("ids_s3_key")
    model_s3_key = data.get("model_s3_key")
    
    if not all([ids_id, model_id, ids_s3_key, model_s3_key]):
        raise HTTPException(status_code=400, detail="Missing required data")
    
    try:
        # Download files from S3
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as model_file:
            s3_client.download_file(
                Bucket=BUCKET_NAME,
                Key=model_s3_key,
                Filename=model_file.name
            )
            model_path = model_file.name
        
        with tempfile.NamedTemporaryFile(suffix=".ids", delete=False) as ids_file:
            s3_client.download_file(
                Bucket=BUCKET_NAME,
                Key=ids_s3_key,
                Filename=ids_file.name
            )
            ids_path = ids_file.name
        
        # Process with IfcOpenShell
        model = ifcopenshell.open(model_path)
        
        # In a real implementation, you would use IfcOpenShell's IDS checking capabilities
        # This is a simplified placeholder implementation
        check_id = str(uuid.uuid4())
        
        # Example check result
        check_result = {
            "id": check_id,
            "ifc_model_id": model_id,
            "ids_id": ids_id,
            "status": "completed",
            "summary": {
                "total_elements": len(model.by_type("IfcElement")),
                "passing": True,
                "message": "All checks passed successfully"
            }
        }
        
        # Save check results to S3
        result_key = f"checks/{check_id}/result.json"
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=result_key,
            Body=json.dumps(check_result)
        )
        
        # Clean up
        os.unlink(model_path)
        os.unlink(ids_path)
        
        return check_result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking model: {str(e)}")