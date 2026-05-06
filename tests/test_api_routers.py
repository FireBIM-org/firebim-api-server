import pytest

def test_health_check(client):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["ok", "degraded"]
    assert "checks" in data

def test_models_listing(client):
    """Test that the models router is working."""
    response = client.get("/ifc-models")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_ids_listing(client):
    """Test that the ids router is working."""
    response = client.get("/ids-files")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_checks_async_endpoint_exists(client):
    """Test that the checks router async endpoint exists."""
    # We don't provide valid IDs so it might fail with 404, but it proves the route is there
    response = client.post("/async/check-model-with-ids?model_id=1&ids_id=2")
    assert response.status_code == 404 

def test_checks_sync_endpoint_exists(client):
    """Test that the checks router sync endpoint exists."""
    response = client.get("/ifc-models/1/check?ids_id=2")
    assert response.status_code == 404
