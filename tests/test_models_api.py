import io

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

    # Check S3 upload
    mock_s3.put_object.assert_called_once()

    # Check Celery task
    mock_celery.send_task.assert_called_once_with(
        "process_model", args=[data["id"], mock_s3.put_object.call_args[1]["Key"]]
    )


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

    # Mock download_file to do nothing
    mock_s3.download_file.return_value = None

    # We also need to mock os.remove if FileResponse is cleaned up, or just let it fail if it doesn't exist
    # Actually FileResponse will try to open the file.
    # In download_model endpoint:
    # local_path = f"/tmp/{model.filename}"
    # s3_client.download_file(Bucket=BUCKET_NAME, Key=model.s3_key, Filename=local_path)
    # return FileResponse(path=local_path, filename=model.filename, media_type="application/octet-stream")

    # To make this work, we should create the file in mock_s3.download_file
    def side_effect(Bucket, Key, Filename):
        with open(Filename, "wb") as f:
            f.write(b"IFC CONTENT")

    mock_s3.download_file.side_effect = side_effect

    response = client.get(f"/ifc-models/{model.id}/download")
    assert response.status_code == 200
    assert response.content == b"IFC CONTENT"
    assert response.headers["content-disposition"] == 'attachment; filename="download.ifc"'

    mock_s3.download_file.assert_called_once()
