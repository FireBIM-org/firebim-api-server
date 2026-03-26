from sqlmodel import Session, SQLModel, create_engine

from firebim_common.config import settings

POSTGRES_USER = settings.POSTGRES_USER
POSTGRES_PASSWORD = settings.POSTGRES_PASSWORD.get_secret_value()
POSTGRES_HOST = settings.POSTGRES_HOST
POSTGRES_PORT = settings.POSTGRES_PORT
POSTGRES_DB = settings.POSTGRES_DB

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

engine = create_engine(DATABASE_URL)


def get_db():
    """Dependency for database session management."""
    with Session(engine) as session:
        yield session


def init_db():
    """Initialize the database by creating all tables."""
    SQLModel.metadata.create_all(bind=engine)
