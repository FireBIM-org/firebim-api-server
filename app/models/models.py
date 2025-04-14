from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean
from datetime import datetime, timezone
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))

class Model(Base):
    __tablename__ = "models"

    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    s3_key = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))

class IdsFile(Base):
    __tablename__ = "ids_files"

    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    s3_key = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))

class CheckResult(Base):
    __tablename__ = "check_results"

    id = Column(String, primary_key=True)
    model_id = Column(String, ForeignKey("models.id"), nullable=False)
    ids_id = Column(String, ForeignKey("ids_files.id"), nullable=False)
    result_json = Column(String, nullable=True)  # Store the result as JSON string
    status = Column(String, nullable=False)  # 'pending', 'completed', 'failed'
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))