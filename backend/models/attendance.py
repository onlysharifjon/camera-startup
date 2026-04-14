from sqlalchemy import Column, String, DateTime, Float, ForeignKey, Date, Enum, Integer
from sqlalchemy.orm import relationship
from datetime import datetime
import enum, uuid
from backend.core.database import Base


class AttendanceStatus(str, enum.Enum):
    present   = "present"     # o'z vaqtida keldi
    late      = "late"        # kech keldi
    early_out = "early_out"   # erta ketdi
    absent    = "absent"      # kelmadi


class Attendance(Base):
    """
    Bir xodimning bir kun uchun davomat yozuvi.
    Kuniga bir marta yaratiladi — qancha ko'rinmasin.
    """
    __tablename__ = "attendance"

    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String, ForeignKey("employees.id"), nullable=False)
    date        = Column(Date, nullable=False)

    checkin_time       = Column(DateTime, nullable=True)   # birinchi ko'ringan vaqt
    checkout_time      = Column(DateTime, nullable=True)   # oxirgi ko'ringan vaqt
    checkin_image      = Column(String(255), default="")
    checkout_image     = Column(String(255), default="")
    checkin_camera_id  = Column(String(100), default="")

    status             = Column(String(20), default=AttendanceStatus.present)
    late_minutes       = Column(Float, default=0)
    early_out_minutes  = Column(Float, default=0)
    work_hours         = Column(Float, default=0)

    # Kunda necha marta aniqlangani (DetectionLog ga asoslanadi)
    detection_count    = Column(Integer, default=0)

    note       = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    employee        = relationship("Employee",      back_populates="attendances")
    detection_logs  = relationship("DetectionLog",  back_populates="attendance",
                                   order_by="DetectionLog.detected_at",
                                   cascade="all, delete-orphan")


class DetectionLog(Base):
    """
    Har bir yuz aniqlash hodisasi.
    Bir xodim kuniga 50 marta aniqlansa — 50 ta yozuv.
    Attendance jadvalida esa shu kun uchun faqat 1 ta yozuv qoladi.
    """
    __tablename__ = "detection_logs"

    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id   = Column(String, ForeignKey("employees.id"), nullable=False)
    attendance_id = Column(String, ForeignKey("attendance.id"), nullable=True)
    camera_id     = Column(String(100), default="")
    detected_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    snapshot_path = Column(String(255), default="")

    employee   = relationship("Employee",   back_populates="detection_logs")
    attendance = relationship("Attendance", back_populates="detection_logs")
