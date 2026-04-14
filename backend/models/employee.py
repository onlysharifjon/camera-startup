from sqlalchemy import Column, String, Boolean, DateTime, Text, Time
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.core.database import Base
import uuid

class Employee(Base):
    __tablename__ = "employees"

    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    full_name    = Column(String(100), nullable=False)
    position     = Column(String(100), default="")
    department   = Column(String(100), default="")
    phone        = Column(String(20),  default="")
    is_active    = Column(Boolean, default=True)

    # Work schedule
    checkin_time  = Column(String(5), default="09:00")   # "HH:MM"
    checkout_time = Column(String(5), default="18:00")   # "HH:MM"
    work_days     = Column(String(20), default="1,2,3,4,5")  # Mon-Fri

    # Face data path
    face_image_path    = Column(String(255), default="")
    face_encoding_path = Column(String(255), default="")
    face_enrolled      = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    attendances     = relationship("Attendance",    back_populates="employee", lazy="select")
    detection_logs  = relationship("DetectionLog", back_populates="employee", lazy="select")
