from sqlalchemy import Column, String, Boolean, DateTime
from datetime import datetime
import uuid
from backend.core.database import Base

DEFAULT_FEATURES = '{"ptz":false,"ir":true,"audio":false,"motion":true,"zoom":false}'

class Camera(Base):
    __tablename__ = "cameras"

    id       = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name     = Column(String(100), nullable=False)
    url      = Column(String(500), nullable=False)   # rtsp:// or v380://
    type     = Column(String(20),  default="rtsp")   # rtsp | v380 | http
    location = Column(String(100), default="")
    enabled  = Column(Boolean, default=True)
    features = Column(String, default=DEFAULT_FEATURES)   # JSON: ptz,ir,audio,motion,zoom
    created_at = Column(DateTime, default=datetime.utcnow)
