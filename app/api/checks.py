import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.auth.jwt import get_current_active_user
from app.models.database import get_db
from app.models.models import CheckResult, IdsFile, IfcModel, User
from app.workers.celery_app import celery_app

router = APIRouter(tags=["checks"])


@router.post("/async/check-model-with-ids", status_code=202)
async def async_check_model_with_ids(
    model_id: str, ids_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Asynchronously check a model against an IDS file.
    Returns immediately with a task ID for polling the result.

    This endpoint demonstrates the first requested pattern:
    "Call that returns immediately with id for polling if result is ready. Task is sent to worker."
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

    # Create a check result record
    check_result = CheckResult(ifc_model_id=model_id, ids_id=ids_id, status="pending")
    db.add(check_result)
    db.commit()
    db.refresh(check_result)

    # Send task to Celery worker
    task = celery_app.send_task("check_model_with_ids", args=[ids_id, model_id, ids_file.s3_key, model.s3_key])

    # Update check result with task ID
    check_result.task_id = task.id
    check_result.status = "processing"
    db.commit()

    return {
        "check_id": check_result.id,
        "task_id": task.id,
        "status": "processing",
        "message": "Task has been queued. Use the check_id to poll for results.",
    }


@router.get("/async/check-result/{check_id}")
async def get_async_check_result(
    check_id: str,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get the result of an asynchronous check operation by its ID.
    This is the polling endpoint to check if a result is ready.
    """
    # Get check result from DB
    check_result = db.query(CheckResult).filter(CheckResult.id == check_id).first()
    if not check_result:
        raise HTTPException(status_code=404, detail="Check result not found")

    # Verify ownership through model and IDS file
    model = db.query(IfcModel).filter(IfcModel.id == check_result.ifc_model_id).first()
    if not model or model.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this check result")

    # Check result status
    if check_result.status == "completed" and check_result.result_json:
        # Return the stored result
        return JSONResponse(content=json.loads(check_result.result_json))
    elif check_result.status == "failed":
        return {"status": "failed", "message": "Check operation failed"}
    elif check_result.task_id:
        # Check task status with Celery
        task_result = celery_app.AsyncResult(check_result.task_id)

        if task_result.ready():
            result = task_result.get()

            # Update the database record
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


@router.get("/ifc-models/{model_id}/check")
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

    This endpoint demonstrates the second requested pattern:
    "Call that waits for the worker to return with the result."
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

    # Create a check result record
    check_result = CheckResult(ifc_model_id=model_id, ids_id=ids_id, status="processing")
    db.add(check_result)
    db.commit()
    db.refresh(check_result)

    # Send task to Celery worker
    task = celery_app.send_task("check_model_with_ids", args=[ids_id, model_id, ids_file.s3_key, model.s3_key])

    # Update check result with task ID
    check_result.task_id = task.id
    db.commit()

    # Wait for the result with timeout
    start_time = time.time()
    while time.time() - start_time < timeout:
        # Check if task is complete
        task_result = celery_app.AsyncResult(task.id)
        if task_result.ready():
            result = task_result.get()

            # Update the database record
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

        # Wait a bit before checking again
        time.sleep(1)

    # If we get here, we've timed out
    check_result.status = "failed"
    db.commit()
    raise HTTPException(status_code=504, detail="Operation timed out")
