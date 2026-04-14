from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, datetime
from typing import Optional

from backend.core.database              import get_db
from backend.models.attendance          import Attendance, DetectionLog
from backend.models.employee            import Employee
from backend.services.attendance_service import attendance_service

router = APIRouter(prefix="/attendance", tags=["attendance"])


@router.get("/today/stats")
async def today_stats(db: AsyncSession = Depends(get_db)):
    """Bugungi umumiy statistika."""
    return await attendance_service.today_stats(db)


@router.get("/today")
async def today_list(db: AsyncSession = Depends(get_db)):
    """Bugun kelgan xodimlar ro'yxati (har biri 1 ta yozuv)."""
    return {"list": await attendance_service.today_list(db)}


@router.get("/logs")
async def detection_logs(
    employee_id: Optional[str] = None,
    date:        Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Kun bo'yi barcha aniqlanish vaqtlari (DetectionLog).
    Bir xodim 20 marta aniqlangan bo'lsa — 20 ta yozuv ko'rinadi.

    Params:
        employee_id: filter by employee (optional)
        date:        YYYY-MM-DD format (default: today)
    """
    logs = await attendance_service.detection_logs(db, employee_id, date)
    return {
        "date":  date or datetime.now().strftime("%Y-%m-%d"),
        "count": len(logs),
        "logs":  logs,
    }


@router.get("/logs/{employee_id}")
async def employee_logs(
    employee_id: str,
    date:        Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Bitta xodimning kun bo'yi barcha aniqlanish vaqtlari.
    Harbiy nazorat uchun — qaysi vaqtlarda ko'ringani.
    """
    emp = await db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    target = date or datetime.now().strftime("%Y-%m-%d")
    logs   = await attendance_service.detection_logs(db, employee_id, target)

    # Bugungi attendance yozuvini ham olish
    att_stmt = select(Attendance).where(
        Attendance.employee_id == employee_id,
        Attendance.date        == datetime.fromisoformat(target).date(),
    )
    att = (await db.execute(att_stmt)).scalar_one_or_none()

    return {
        "employee_id":  employee_id,
        "name":         emp.full_name,
        "position":     emp.position,
        "date":         target,
        "attendance": {
            "checkin_time":    att.checkin_time.strftime("%H:%M:%S")  if att and att.checkin_time  else None,
            "checkout_time":   att.checkout_time.strftime("%H:%M:%S") if att and att.checkout_time else None,
            "status":          att.status          if att else None,
            "work_hours":      att.work_hours       if att else 0,
            "late_minutes":    att.late_minutes     if att else 0,
            "detection_count": att.detection_count  if att else 0,
        } if att else None,
        "detections": logs,
    }


@router.get("/history")
async def history(
    employee_id: Optional[str] = None,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Davomat tarixi (kunlik yig'ma)."""
    rows = await attendance_service.history(db, employee_id, date_from, date_to)
    return {"history": rows}


@router.patch("/{att_id}")
async def update_attendance(
    att_id:        str,
    checkin_time:  Optional[str] = None,
    checkout_time: Optional[str] = None,
    note:          Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Davomat vaqtini qo'lda tahrirlash."""
    att = await db.get(Attendance, att_id)
    if not att:
        raise HTTPException(404, "Record not found")

    today_str = att.date.isoformat()
    if checkin_time:
        att.checkin_time  = datetime.fromisoformat(f"{today_str}T{checkin_time}:00")
    if checkout_time:
        att.checkout_time = datetime.fromisoformat(f"{today_str}T{checkout_time}:00")
    if note is not None:
        att.note = note

    if att.checkin_time and att.checkout_time:
        att.work_hours = round(
            (att.checkout_time - att.checkin_time).total_seconds() / 3600, 2
        )

    await db.commit()
    return {"updated": True}
