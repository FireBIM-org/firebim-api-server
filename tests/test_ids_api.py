from io import BytesIO


def test_upload_ids(client, mock_s3):
    """Test uploading an IDS file."""
    ids_content = b"<ids>test</ids>"
    file = ("test.ids", BytesIO(ids_content))

    response = client.post("/ids-files", files={"file": file})

    assert response.status_code == 201
    assert "id" in response.json()

    # Verify S3 upload was called
    mock_s3.put_object.assert_called_once()


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

    # Mock download_file to create a dummy file
    def side_effect(Bucket, Key, Filename):
        with open(Filename, "wb") as f:
            f.write(b"<ids>downloaded</ids>")

    mock_s3.download_file.side_effect = side_effect

    response = client.get(f"/ids-files/{ids_file.id}/download")

    assert response.status_code == 200
    assert response.content == b"<ids>downloaded</ids>"
    mock_s3.download_file.assert_called_once()
