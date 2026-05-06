import os
import pytest

# Set required environment variables before importing app
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_PORT"] = "5433"
os.environ["POSTGRES_USER"] = "testuser"
os.environ["POSTGRES_PASSWORD"] = "testpass"
os.environ["POSTGRES_DB"] = "testdb"
os.environ["S3_ENDPOINT_URL"] = "http://localhost:9000"
os.environ["S3_BUCKET_NAME"] = "test-bucket"
os.environ["S3_ACCESS_KEY_ID"] = "test-access-key"
os.environ["S3_SECRET_ACCESS_KEY"] = "test-secret-key"
os.environ["S3_REGION"] = "us-east-1"
os.environ["CELERY_BROKER_URL"] = "redis://localhost:6379/0"
os.environ["CELERY_RESULT_BACKEND"] = "redis://localhost:6379/0"
os.environ["FUSEKI_HOST"] = "http://localhost:3030"

from typing import Generator
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, StaticPool
from sqlalchemy.engine import Engine
from unittest.mock import MagicMock, patch, AsyncMock

from app.main import app
from app.models.database import get_db
from app.models.models import User

# Remove py_pglite dependencies as they require npm
# from py_pglite.manager import PGliteManager
# from py_pglite.config import PGliteConfig

@pytest.fixture(scope="session")
def engine():
    """Get SQLAlchemy engine with sqlite memory database."""
    # check_same_thread=False is needed for sqlite in multi-threaded environments like FastAPI tests
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

@pytest.fixture(name="session")
def session_fixture(engine: Engine):
    # Create all tables for each test
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        yield session
    
    # Drop all tables after each test to ensure isolation
    SQLModel.metadata.drop_all(engine)

@pytest.fixture(name="test_user")
def test_user_fixture(session: Session):
    user = User(
        authentik_sub="test-sub",
        username="testuser",
        email="test@example.org",
        is_active=True
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user

@pytest.fixture(name="client")
def client_fixture(session: Session, test_user: User, engine: Engine):
    def get_db_override():
        yield session

    def get_current_active_user_override():
        return test_user

    app.dependency_overrides[get_db] = get_db_override
    # We need to override get_current_active_user from app.api.models
    # But it is imported from app.auth.jwt
    from app.auth.jwt import get_current_active_user
    app.dependency_overrides[get_current_active_user] = get_current_active_user_override
    
    with patch("app.models.database.engine", engine), \
         patch("app.api.health.engine", engine), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("app.models.database.init_db", return_value=None):
        
        # Mock httpx for health check
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"issuer": "http://mock-auth"}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        
        with TestClient(app) as client:
            yield client
    
    app.dependency_overrides.clear()

@pytest.fixture(autouse=True)
def mock_s3():
    with patch("app.core.s3.s3_client") as mock:
        with patch("app.main.s3_client", mock), \
             patch("app.api.models.s3_client", mock), \
             patch("app.api.ids.s3_client", mock), \
             patch("app.api.health.s3_client", mock):
            yield mock

@pytest.fixture(autouse=True)
def mock_celery():
    # Use where it is imported in the code being tested
    with patch("app.main.celery_app") as mock:
        with patch("app.api.models.celery_app", mock), \
             patch("app.api.checks.celery_app", mock), \
             patch("app.api.health.celery_app", mock):
            # Mock celery_app.send_task to return a mock task with an id
            mock_task = MagicMock()
            mock_task.id = "test-task-id"
            mock.send_task.return_value = mock_task
            yield mock
