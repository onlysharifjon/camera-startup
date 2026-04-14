"""
AttendanceService
-----------------
Yuz tanilganda avtomatik davomat yozadi:
  - Birinchi ko'rinish → checkin
  - Keyingi ko'rinish (soat 12 dan keyin) → checkout
  - Kech kelish, erta ketish hisoblanadi
  - Bugungi statistika qaytaradi
"""
import logging
from datetime import datetime, date, timedelta, time
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.models.attendance import Attendance, AttendanceStatus
from backend.models.employee   import Employee
from backend.core.config       import settings

log = logging.getLogger(__name__)

# Employee bo'yicha bugun birinchi ko'ringan vaqt (RAM cache)
_today_seen: dict[str, datetime] = {}

class AttendanceService:

    async def process_recognition(
        self,
        db: AsyncSession,
        employee_id: str,
        camera_id: str,
        snapshot_path: str,
    ) -> Optional[dict]:
        """
        Yuz tanilganda chaqiriladi.
        Checkin yoki checkout yozadi.
        """
        now   = datetime.now()
        today = now.date()

        emp = await db.get(Employee, employee_id)
        if not emp or not emp.is_active:
            return None

        # Bugungi davomat yozuvini topish
        stmt = select(Attendance).where(
            Attendance.employee_id == employee_id,
            Attendance.date == today,
        )
        result     = await db.execute(stmt)
        attendance = result.scalar_one_or_none()

        action = None

        # ── CHECKIN ───────────────────────────────────────────────────────────
        if attendance is None:
            # Jadval bo'yicha kelish vaqti
            sched_in = self._parse_time(emp.checkin_time or settings.DEFAULT_CHECKIN_TIME)
            late_min  = self._minutes_diff(sched_in, now.time())

            status = (
                AttendanceStatus.late if late_min > settings.LATE_THRESHOLD_MINUTES
                else AttendanceStatus.present
            )

            attendance = Attendance(
                employee_id       = employee_id,
                date              = today,
                checkin_time      = now,
                checkin_image     = snapshot_path,
                checkin_camera_id = camera_id,
                status            = status,
                late_minutes      = max(0, late_min),
            )
            db.add(attendance)
            await db.commit()
            await db.refresh(attendance)

            action = "checkin"
            log.info(f"[ATT] CHECKIN  {emp.full_name}  {now.strftime('%H:%M')}  status={status}")

        # ── CHECKOUT ──────────────────────────────────────────────────────────
        elif attendance.checkout_time is None and now.hour >= 12:
            sched_out  = self._parse_time(emp.checkout_time or settings.DEFAULT_CHECKOUT_TIME)
            early_min  = self._minutes_diff(now.time(), sched_out)
            work_hours = round((now - attendance.checkin_time).total_seconds() / 3600, 2)

            attendance.checkout_time      = now
            attendance.checkout_image     = snapshot_path
            attendance.work_hours         = work_hours
            attendance.early_out_minutes  = max(0, early_min)

            if early_min > settings.LATE_THRESHOLD_MINUTES:
                attendance.status = AttendanceStatus.early_out

            await db.commit()
            action = "checkout"
            log.info(f"[ATT] CHECKOUT {emp.full_name}  {now.strftime('%H:%M')}  hours={work_hours}")

        else:
            return None  # Already fully recorded today

        return {
            "action":      action,
            "employee_id": employee_id,
            "name":        emp.full_name,
            "position":    emp.position,
            "time":        now.isoformat(),
            "status":      attendance.status,
            "work_hours":  attendance.work_hours,
        }

    # ── Statistics ────────────────────────────────────────────────────────────

    async def today_stats(self, db: AsyncSession) -> dict:
        today = date.today()

        total_emp = await db.scalar(
            select(func.count()).where(Employee.is_active == True)
        )
        present = await db.scalar(
            select(func.count()).where(
                Attendance.date == today,
                Attendance.checkin_time.isnot(None),
            )
        )
        late = await db.scalar(
            select(func.count()).where(
                Attendance.date == today,
                Attendance.status == AttendanceStatus.late,
            )
        )
        left = await db.scalar(
            select(func.count()).where(
                Attendance.date == today,
                Attendance.checkout_time.isnot(None),
            )
        )
        absent = (total_emp or 0) - (present or 0)

        return {
            "date":        today.isoformat(),
            "total":       total_emp  or 0,
            "present":     present    or 0,
            "absent":      max(0, absent),
            "late":        late       or 0,
            "checked_out": left       or 0,
        }

    async def today_list(self, db: AsyncSession) -> list[dict]:
        today = date.today()
        stmt  = (
            select(Attendance, Employee)
            .join(Employee, Attendance.employee_id == Employee.id)
            .where(Attendance.date == today)
            .order_by(Attendance.checkin_time.asc())
        )
        rows = await db.execute(stmt)
        result = []
        for att, emp in rows:
            result.append({
                "id":             att.id,
                "employee_id":    emp.id,
                "name":           emp.full_name,
                "position":       emp.position,
                "department":     emp.department,
                "checkin_time":   att.checkin_time.strftime("%H:%M") if att.checkin_time else None,
                "checkout_time":  att.checkout_time.strftime("%H:%M") if att.checkout_time else None,
                "status":         att.status,
                "late_minutes":   att.late_minutes,
                "work_hours":     att.work_hours,
                "checkin_image":  att.checkin_image,
                "checkout_image": att.checkout_image,
            })
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_time(t: str) -> time:
        h, m = map(int, t.split(":"))
        return time(h, m)

    @staticmethod
    def _minutes_diff(a: time, b: time) -> float:
        """b - a in minutes (positive if b is after a)."""
        a_min = a.hour * 60 + a.minute
        b_min = b.hour * 60 + b.minute
        return b_min - a_min


attendance_service = AttendanceService()
