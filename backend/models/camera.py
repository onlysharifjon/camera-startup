from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from backend.core.database import Base

DEFAULT_FEATURES = '{"ptz":false,"ir":true,"audio":false,"motion":true,"zoom":false}'

class Camera(Base):
    __tablename__ = "cameras"

    id        = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name      = Column(String(100), nullable=False)
    url       = Column(String(500), nullable=False)
    type      = Column(String(20),  default="rtsp")
    location  = Column(String(100), default="")
    enabled   = Column(Boolean, default=True)
    features  = Column(String, default=DEFAULT_FEATURES)
    brand_id  = Column(String, ForeignKey("brands.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand", back_populates="cameras")
