from sqlalchemy import Column, String, DateTime, Float, ForeignKey, Date, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum, uuid
from backend.core.database import Base

class AttendanceStatus(str, enum.Enum):
    present   = "present"    # o'z vaqtida keldi
    late      = "late"       # kech keldi
    early_out = "early_out"  # erta ketdi
    absent    = "absent"     # kelmadi

class Attendance(Base):
    __tablename__ = "attendance"

    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String, ForeignKey("employees.id"), nullable=False)
    date        = Column(Date, nullable=False)

    checkin_time      = Column(DateTime, nullable=True)
    checkout_time     = Column(DateTime, nullable=True)
    checkin_image     = Column(String(255), default="")
    checkout_image    = Column(String(255), default="")
    checkin_camera_id = Column(String(100), default="")   # qaysi kamera aniqladi

    status          = Column(String(20), default=AttendanceStatus.present)
    late_minutes    = Column(Float, default=0)
    early_out_minutes = Column(Float, default=0)
    work_hours      = Column(Float, default=0)

    note            = Column(String(255), default="")
    created_at      = Column(DateTime, default=datetime.utcnow)

    employee = relationship("Employee", back_populates="attendances")
