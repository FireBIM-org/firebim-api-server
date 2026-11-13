import logging
import os

from alembic.config import Config
from sqlmodel import Session, SQLModel, create_engine

from alembic import command
from app.core.config import settings

logger = logging.getLogger(__name__)

# Get PostgreSQL connection details from environment variables
POSTGRES_USER = settings.POSTGRES_USER
POSTGRES_PASSWORD = settings.POSTGRES_PASSWORD.get_secret_value()
POSTGRES_HOST = settings.POSTGRES_HOST
POSTGRES_PORT = settings.POSTGRES_PORT
POSTGRES_DB = settings.POSTGRES_DB

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# Create the SQLModel engine. pre_ping replaces pooled connections that died
# when Postgres restarted or failed over (CNPG does this on every upgrade),
# instead of failing the next request that checks one out.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


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

    # Create all tables
    SQLModel.metadata.create_all(bind=engine)


def run_migrations():
    """Run alembic migrations programmatically"""
    logger.info("Running database migrations")

    # Calculate the path to alembic.ini relative to this file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # database.py is in app/models/, so we go up two levels to reach firebim-api-server/
    root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
    ini_path = os.path.join(root_dir, "alembic.ini")

    if not os.path.exists(ini_path):
        logger.error(f"alembic.ini not found at {ini_path}")
        # Fallback to current working directory
        ini_path = "alembic.ini"
        if not os.path.exists(ini_path):
            raise FileNotFoundError("Could not find alembic.ini")

    alembic_cfg = Config(ini_path)
    command.upgrade(alembic_cfg, "head")
    logger.info("Database migrations completed successfully")
