from io import BytesIO

from botocore.response import StreamingBody


def test_upload_ids(client, mock_s3):
    """Test uploading an IDS file."""
    ids_content = b"<ids>test</ids>"
    file = ("test.ids", BytesIO(ids_content))

    response = client.post("/ids-files", files={"file": file})

    assert response.status_code == 201
    assert "id" in response.json()

    # Verify the upload was streamed to S3
    mock_s3.put_object.assert_not_called()
    mock_s3.upload_fileobj.assert_called_once()
    _, bucket, key = mock_s3.upload_fileobj.call_args[0]
    assert (bucket, key) == ("test-bucket", f"ids/{response.json()['id']}/test.ids")
    assert mock_s3.upload_fileobj.call_args[1]["ExtraArgs"] == {"ContentType": "application/xml"}


def test_upload_ids_invalid_extension(client):
    """Test uploading a file with invalid extension."""
    file = ("test.txt", BytesIO(b"test"))

    response = client.post("/ids-files", files={"file": file})

    assert response.status_code == 400
    assert response.json()["detail"] == "File must be an .ids (XML) file"


def test_get_ids(client, session, test_user):
    """Test getting IDS file metadata."""
    # First upload an IDS file to have something in DB
    from app.models.models import IdsFile

    ids_file = IdsFile(id="test-ids-id", filename="test.ids", s3_key="ids/test-ids-id/test.ids", user_id=test_user.id)
    session.add(ids_file)
    session.commit()

    response = client.get(f"/ids-files/{ids_file.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == ids_file.id
    assert data["filename"] == "test.ids"


def test_get_ids_not_found(client):
    """Test getting non-existent IDS file."""
    response = client.get("/ids-files/non-existent-id")
    assert response.status_code == 404


def test_get_ids_unauthorized(client, session):
    """Test getting IDS file belonging to another user."""
    from app.models.models import IdsFile, User

    other_user = User(authentik_sub="other-sub", username="otheruser", email="other@example.org", is_active=True)
    session.add(other_user)
    session.commit()

    ids_file = IdsFile(
        id="other-ids-id", filename="other.ids", s3_key="ids/other-ids-id/other.ids", user_id=other_user.id
    )
    session.add(ids_file)
    session.commit()

    response = client.get(f"/ids-files/{ids_file.id}")
    assert response.status_code == 403


def test_get_ids_files(client, session, test_user):
    """Test listing IDS files."""
    from app.models.models import IdsFile

    ids_file = IdsFile(id="ids-1", filename="ids1.ids", s3_key="ids/ids-1/ids1.ids", user_id=test_user.id)
    session.add(ids_file)
    session.commit()

    response = client.get("/ids-files")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(item["id"] == "ids-1" for item in data)


def test_download_ids(client, session, test_user, mock_s3):
    """Test downloading an IDS file."""
    from app.models.models import IdsFile

    ids_file = IdsFile(
        id="ids-download",
        filename="test_download.ids",
        s3_key="ids/ids-download/test_download.ids",
        user_id=test_user.id,
    )
    session.add(ids_file)
    session.commit()

    content = b"<ids>downloaded</ids>"
    mock_s3.get_object.return_value = {"Body": StreamingBody(BytesIO(content), len(content)), "ContentLength": 21}

    response = client.get(f"/ids-files/{ids_file.id}/download")

    assert response.status_code == 200
    assert response.content == b"<ids>downloaded</ids>"
    assert response.headers["content-type"].startswith("application/xml")
    assert response.headers["content-length"] == "21"
    mock_s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="ids/ids-download/test_download.ids")
    mock_s3.download_file.assert_not_called()
