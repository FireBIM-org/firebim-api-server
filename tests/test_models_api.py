import io

from botocore.response import StreamingBody
from sqlmodel import Session

from app.models.models import IfcModel, User


def test_upload_model(client, mock_s3, mock_celery):
    # Mock file content
    content = b"TEST IFC CONTENT"
    file = ("test_model.ifc", io.BytesIO(content))

    response = client.post("/ifc-models", files={"file": file})

    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["task_id"] == "test-task-id"
    assert data["status"] == "processing"

    # Check S3 upload: streamed from the spooled file, never read into memory
    mock_s3.put_object.assert_not_called()
    mock_s3.upload_fileobj.assert_called_once()
    fileobj, bucket, key = mock_s3.upload_fileobj.call_args[0]
    assert bucket == "test-bucket"
    assert key == f"models/{data['id']}/test_model.ifc"

    # Check Celery task
    mock_celery.send_task.assert_called_once_with("process_model", args=[data["id"], key])


def test_upload_model_sanitizes_filename(client, mock_s3, session: Session):
    file = (r"..\evil dir/../Bygning ÆØÅ #1.ifc", io.BytesIO(b"IFC"))

    response = client.post("/ifc-models", files={"file": file})

    assert response.status_code == 201
    model_id = response.json()["id"]
    key = mock_s3.upload_fileobj.call_args[0][2]
    assert key == f"models/{model_id}/Bygning_A_1.ifc"
    stored = session.get(IfcModel, model_id)
    assert stored.filename == "Bygning ÆØÅ #1.ifc"


def test_upload_model_too_large(client, mock_s3, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 10)
    file = ("big.ifc", io.BytesIO(b"x" * 11))

    response = client.post("/ifc-models", files={"file": file})

    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
    mock_s3.upload_fileobj.assert_not_called()


def test_upload_model_invalid_extension(client):
    file = ("test_model.txt", io.BytesIO(b"content"))

    response = client.post("/ifc-models", files={"file": file})

    assert response.status_code == 400
    assert response.json()["detail"] == "File must be an IFC (STEP) file"


def test_get_model(client, session: Session, test_user: User):
    # Create model in DB
    model = IfcModel(
        id="test-model-id",
        filename="test.ifc",
        s3_key="models/test-model-id/test.ifc",
        user_id=test_user.id,
        processing_status="processing",
        task_id="test-task-id",
    )
    session.add(model)
    session.commit()

    response = client.get(f"/ifc-models/{model.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == model.id
    assert data["filename"] == "test.ifc"
    assert data["status"] == "processing"


def test_get_model_not_found(client):
    response = client.get("/ifc-models/non-existent-id")
    assert response.status_code == 404
    assert response.json()["detail"] == "Model not found"


def test_get_model_unauthorized(client, session: Session):
    # Create model belonging to another user
    model = IfcModel(
        id="other-user-model",
        filename="other.ifc",
        s3_key="models/other/other.ifc",
        user_id="other-user-id",
        processing_status="pending",
    )
    session.add(model)
    session.commit()

    response = client.get(f"/ifc-models/{model.id}")
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to access this model"


def test_get_models(client, session: Session, test_user: User):
    # Create models in DB
    model1 = IfcModel(
        id="model1", filename="model1.ifc", s3_key="s3-1", user_id=test_user.id, processing_status="completed"
    )
    model2 = IfcModel(
        id="model2", filename="model2.ifc", s3_key="s3-2", user_id=test_user.id, processing_status="pending"
    )
    session.add(model1)
    session.add(model2)
    session.commit()

    response = client.get("/ifc-models")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(m["id"] == "model1" for m in data)
    assert any(m["id"] == "model2" for m in data)


def test_download_model(client, session: Session, test_user: User, mock_s3):
    # Create model in DB
    model = IfcModel(
        id="download-id",
        filename="download.ifc",
        s3_key="models/download-id/download.ifc",
        user_id=test_user.id,
        processing_status="completed",
    )
    session.add(model)
    session.commit()

    content = b"IFC CONTENT"
    mock_s3.get_object.return_value = {
        "Body": StreamingBody(io.BytesIO(content), len(content)),
        "ContentLength": len(content),
    }

    response = client.get(f"/ifc-models/{model.id}/download")
    assert response.status_code == 200
    assert response.content == b"IFC CONTENT"
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"download.ifc\"; filename*=UTF-8''download.ifc"
    )

    # Streamed straight from S3: nothing is written to local disk
    mock_s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="models/download-id/download.ifc")
    mock_s3.download_file.assert_not_called()


def test_download_model_missing_object(client, session: Session, test_user: User, mock_s3):
    from botocore.exceptions import ClientError

    model = IfcModel(
        id="missing-id",
        filename="missing.ifc",
        s3_key="models/missing-id/missing.ifc",
        user_id=test_user.id,
    )
    session.add(model)
    session.commit()
    mock_s3.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    response = client.get(f"/ifc-models/{model.id}/download")

    assert response.status_code == 404


def test_delete_model(client, session: Session, test_user: User, mock_s3):
    from sqlmodel import select

    from app.models.models import CheckResult, IdsFile, IfcEntity

    # 1. Setup: Create model and related data
    model = IfcModel(
        id="delete-me",
        filename="delete.ifc",
        s3_key="models/delete-me/delete.ifc",
        result_key="results/delete-me/stats.json",
        user_id=test_user.id,
        processing_status="completed",
    )
    session.add(model)

    ids_file = IdsFile(id="some-ids", filename="test.ids", s3_key="ids/test.ids", user_id=test_user.id)
    session.add(ids_file)
    session.commit()

    entity = IfcEntity(global_id="global-1", id="1", model_id=model.id, type="IfcWall")
    session.add(entity)

    check_result = CheckResult(
        ifc_model_id=model.id, ids_id=ids_file.id, result_key="checks/check-1/result.json", status="completed"
    )
    session.add(check_result)
    session.commit()

    # 2. Call DELETE
    response = client.delete(f"/ifc-models/{model.id}")
    assert response.status_code == 204

    # 3. Verify S3 cleanup
    assert mock_s3.delete_object.call_count == 3
    called_keys = [call.kwargs["Key"] for call in mock_s3.delete_object.call_args_list]
    assert "models/delete-me/delete.ifc" in called_keys
    assert "results/delete-me/stats.json" in called_keys
    assert "checks/check-1/result.json" in called_keys

    # 4. Verify DB cleanup
    # We need to refresh or use a new session to be sure, but session is usually shared in tests
    # Actually session.exec(select(...)).first() should be enough
    assert session.exec(select(IfcModel).where(IfcModel.id == model.id)).first() is None
    # Note: Cascade deletion might not work in SQLite without PRAGMA foreign_keys = ON;
    # or if SQLAlchemy doesn't emit the deletes.
    # But let's see.
    assert session.exec(select(IfcEntity).where(IfcEntity.model_id == model.id)).first() is None
    assert session.exec(select(CheckResult).where(CheckResult.ifc_model_id == model.id)).first() is None
