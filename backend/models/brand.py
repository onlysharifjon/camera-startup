from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from backend.core.database import Base


class Brand(Base):
    __tablename__ = "brands"

    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name         = Column(String(100), nullable=False, unique=True)   # papka nomi ham shu
    username     = Column(String(50),  nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    is_superuser = Column(Boolean, default=False)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)

    cameras = relationship("Camera", back_populates="brand", lazy="select")
