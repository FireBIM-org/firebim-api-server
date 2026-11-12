import json
import os
import tempfile
import uuid

import ifcopenshell
import ifctester
import ifctester.reporter
from sqlmodel import Session, select

from app.core.s3 import BUCKET_NAME, s3_client
from app.models.database import engine, get_db
from app.models.models import IfcModel
from app.storage.quota import account_write
from app.workers.celery_app import celery_app


def _put_json(key: str, payload, *, user_id: str | None = None) -> None:
    """Write a JSON result object, reserved against the storage cap (raises when the cap is reached)."""
    body = json.dumps(payload).encode("utf-8")
    with account_write(s3_client, BUCKET_NAME, key, len(body), user_id=user_id):
        s3_client.put_object(Bucket=BUCKET_NAME, Key=key, Body=body)


def _model_owner(model_id) -> str | None:
    with Session(engine) as db:
        return db.exec(select(IfcModel.user_id).where(IfcModel.id == model_id)).first()


@celery_app.task(name="process_model")
def process_model(model_id, s3_key):
    """
    Process an IFC model file asynchronously.
    """
    try:
        # Download file from S3
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as temp_file:
            s3_client.download_file(Bucket=BUCKET_NAME, Key=s3_key, Filename=temp_file.name)
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

        # Save processing results to S3 (attributed to the model's owner)
        result_key = f"results/{model_id}/stats.json"
        _put_json(result_key, stats, user_id=_model_owner(model_id))

        # Clean up
        os.unlink(temp_path)

        # Update database with result information
        db = next(get_db())
        ifc_model = db.exec(select(IfcModel).where(IfcModel.id == model_id)).first()
        if ifc_model:
            ifc_model.processed = True
            ifc_model.result_key = result_key
            db.commit()

        return {"status": "success", "id": model_id, "result_key": result_key, "stats": stats}

    except Exception as e:
        return {"status": "error", "id": model_id, "error": str(e)}


@celery_app.task(name="check_model_with_ids")
def check_model_with_ids(ids_id, model_id, ids_s3_key, model_s3_key):
    """
    Check an IFC model against an IDS file asynchronously.
    """
    try:
        # Download files from S3
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as model_file:
            s3_client.download_file(Bucket=BUCKET_NAME, Key=model_s3_key, Filename=model_file.name)
            model_path = model_file.name

        with tempfile.NamedTemporaryFile(suffix=".ids", delete=False) as ids_file:
            s3_client.download_file(Bucket=BUCKET_NAME, Key=ids_s3_key, Filename=ids_file.name)
            ids_path = ids_file.name

        # Process with IfcOpenShell
        ifc_model = ifcopenshell.open(model_path)

        # Check model against IDS
        check_id = str(uuid.uuid4())
        ids = ifctester.open(ids_path, validate=True)
        ids.validate(ifc_model)

        reporter = ifctester.reporter.Json(ids)
        reporter.report()

        check_result = json.loads(reporter.to_string())

        # Save check results to S3 (no DB row records the owner of checks/*, so they count globally)
        result_key = f"checks/{check_id}/result.json"
        _put_json(result_key, check_result)

        # Clean up
        os.unlink(model_path)
        os.unlink(ids_path)

        return {"status": "success", "id": check_id, "result_key": result_key, "check_result": check_result}

    except Exception as e:
        return {"status": "error", "error": str(e)}
