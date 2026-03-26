import json
import os
import tempfile
import uuid

import boto3
import ifcopenshell
import ifctester
import ifctester.reporter
from firebim_common.celery import celery_app
from firebim_common.config import settings
from firebim_common.database import get_db
from firebim_common.models import IfcModel

# S3 Configuration
s3_client = boto3.client(
    "s3",
    endpoint_url=settings.S3_ENDPOINT_URL,
    aws_access_key_id=settings.S3_ACCESS_KEY_ID,
    aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY.get_secret_value(),
    region_name=settings.S3_REGION,
)

BUCKET_NAME = settings.S3_BUCKET_NAME


@celery_app.task(name="process_model")
def process_model(model_id, s3_key):
    """Process an IFC model file asynchronously."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as temp_file:
            s3_client.download_file(Bucket=BUCKET_NAME, Key=s3_key, Filename=temp_file.name)
            temp_path = temp_file.name

        model = ifcopenshell.open(temp_path)

        stats = {}
        for element in model.by_type("IfcElement"):
            element_type = element.is_a()
            if element_type in stats:
                stats[element_type] += 1
            else:
                stats[element_type] = 1

        result_key = f"results/{model_id}/stats.json"
        s3_client.put_object(Bucket=BUCKET_NAME, Key=result_key, Body=json.dumps(stats))

        os.unlink(temp_path)

        db = next(get_db())
        ifc_model = db.query(IfcModel).filter(IfcModel.id == model_id).first()
        if ifc_model:
            ifc_model.processed = True
            ifc_model.result_key = result_key
            db.commit()

        return {"status": "success", "id": model_id, "result_key": result_key, "stats": stats}

    except Exception as e:
        return {"status": "error", "id": model_id, "error": str(e)}


@celery_app.task(name="check_model_with_ids")
def check_model_with_ids(ids_id, model_id, ids_s3_key, model_s3_key):
    """Check an IFC model against an IDS file asynchronously."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as model_file:
            s3_client.download_file(Bucket=BUCKET_NAME, Key=model_s3_key, Filename=model_file.name)
            model_path = model_file.name

        with tempfile.NamedTemporaryFile(suffix=".ids", delete=False) as ids_file:
            s3_client.download_file(Bucket=BUCKET_NAME, Key=ids_s3_key, Filename=ids_file.name)
            ids_path = ids_file.name

        ifc_model = ifcopenshell.open(model_path)

        check_id = str(uuid.uuid4())
        ids = ifctester.open(ids_path, validate=True)
        ids.validate(ifc_model)

        reporter = ifctester.reporter.Json(ids)
        reporter.report()

        check_result = json.loads(reporter.to_string())

        result_key = f"checks/{check_id}/result.json"
        s3_client.put_object(Bucket=BUCKET_NAME, Key=result_key, Body=json.dumps(check_result))

        os.unlink(model_path)
        os.unlink(ids_path)

        return {"status": "success", "id": check_id, "result_key": result_key, "check_result": check_result}

    except Exception as e:
        return {"status": "error", "error": str(e)}
