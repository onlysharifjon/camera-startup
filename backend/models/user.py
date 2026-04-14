from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from backend.core.database import Base


class User(Base):
    __tablename__ = "users"

    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    full_name    = Column(String(100), nullable=False)
    connected_id = Column(String(100), unique=True, nullable=False)   # tashqi ID (badge, guvohnoma, ...)
    note         = Column(String(255), default="")
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
