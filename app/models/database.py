from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings

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
