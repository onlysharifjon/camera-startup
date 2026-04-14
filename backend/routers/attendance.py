from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, datetime
from typing import Optional

from backend.core.database         import get_db
from backend.models.attendance     import Attendance
from backend.models.employee       import Employee
from backend.services.attendance_service import attendance_service

router = APIRouter(prefix="/attendance", tags=["attendance"])


@router.get("/today/stats")
async def today_stats(db: AsyncSession = Depends(get_db)):
    """Bugungi umumiy statistika."""
    return await attendance_service.today_stats(db)


@router.get("/today")
async def today_list(db: AsyncSession = Depends(get_db)):
    """Bugun kelgan xodimlar ro'yxati."""
    return {"list": await attendance_service.today_list(db)}


@router.get("/history")
async def history(
    employee_id: Optional[str] = None,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Davomat tarixi. Filter: employee_id, date_from, date_to (YYYY-MM-DD)."""
    stmt = select(Attendance, Employee).join(Employee)

    if employee_id:
        stmt = stmt.where(Attendance.employee_id == employee_id)
    if date_from:
        stmt = stmt.where(Attendance.date >= date.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(Attendance.date <= date.fromisoformat(date_to))

    stmt = stmt.order_by(Attendance.date.desc(), Attendance.checkin_time.desc())
    rows = await db.execute(stmt)

    result = []
    for att, emp in rows:
        result.append({
            "id":             att.id,
            "employee_id":    emp.id,
            "name":           emp.full_name,
            "position":       emp.position,
            "department":     emp.department,
            "date":           att.date.isoformat(),
            "checkin_time":   att.checkin_time.strftime("%H:%M")  if att.checkin_time  else None,
            "checkout_time":  att.checkout_time.strftime("%H:%M") if att.checkout_time else None,
            "status":         att.status,
            "late_minutes":   att.late_minutes,
            "work_hours":     att.work_hours,
        })
    return {"history": result}


@router.patch("/{att_id}")
async def update_attendance(
    att_id: str,
    checkin_time:  Optional[str] = None,
    checkout_time: Optional[str] = None,
    note:          Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Davomat vaqtini qo'lda tahrirlash."""
    att = await db.get(Attendance, att_id)
    if not att:
        from fastapi import HTTPException
        raise HTTPException(404, "Record not found")

    today_str = att.date.isoformat()
    if checkin_time:
        att.checkin_time  = datetime.fromisoformat(f"{today_str}T{checkin_time}:00")
    if checkout_time:
        att.checkout_time = datetime.fromisoformat(f"{today_str}T{checkout_time}:00")
    if note:
        att.note = note

    if att.checkin_time and att.checkout_time:
        att.work_hours = round(
            (att.checkout_time - att.checkin_time).total_seconds() / 3600, 2
        )

    await db.commit()
    return {"updated": True}
