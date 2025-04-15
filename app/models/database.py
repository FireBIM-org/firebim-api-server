from sqlmodel import SQLModel, create_engine, Session
import os

from app.core.config import settings

# Get PostgreSQL connection details from environment variables
POSTGRES_USER = settings.POSTGRES_USER
POSTGRES_PASSWORD = settings.POSTGRES_PASSWORD
POSTGRES_HOST = settings.POSTGRES_HOST
POSTGRES_PORT = settings.POSTGRES_PORT
POSTGRES_DB = settings.POSTGRES_DB

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# Create the SQLModel engine
engine = create_engine(DATABASE_URL)

def get_db():
    """
    Dependency for database session management.
    Creates a new database session for each request, and closes it when the request is finished.
    """
    with Session(engine) as session:
        yield session

# Function to initialize the database
def init_db():
    """Initialize the database by creating all tables"""
    # Import models here to avoid circular imports
    from .models import IfcModel, IdsFile, CheckResult, User
    
    # Create all tables
    SQLModel.metadata.create_all(bind=engine)