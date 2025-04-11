from sqlalchemy import Column, String, DateTime, ForeignKey
from datetime import datetime
from .database import Base

class Model(Base):
    __tablename__ = "models"

    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    s3_key = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class IdsFile(Base):
    __tablename__ = "ids_files"

    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    s3_key = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CheckResult(Base):
    __tablename__ = "check_results"

    id = Column(String, primary_key=True)
    model_id = Column(String, ForeignKey("models.id"), nullable=False)
    ids_id = Column(String, ForeignKey("ids_files.id"), nullable=False)
    result_json = Column(String, nullable=True)  # Store the result as JSON string
    status = Column(String, nullable=False)  # 'pending', 'completed', 'failed'
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)