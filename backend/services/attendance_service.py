"""
AttendanceService
-----------------
Harbiy davomat tizimi — quyidagi qoidalar asosida ishlaydi:

ASOSIY QOIDA:
  Bir xodim bir kunda qancha marta aniqlanmasin → FAQAT 1 ta davomat yozuvi.
  Lekin har bir aniqlanish ALOHIDA yoziladi (DetectionLog).

JARAYON:
  1. Yuz aniqlandi
     → DetectionLog ga DOIM yoziladi (vaqt, kamera, rasm)
     → Attendance.detection_count ++ (kunda necha marta ko'ringan)

  2. Birinchi aniqlanish (o'sha kunda attendance yo'q)
     → Attendance yaratiladi: checkin_time = hozir
     → Kech kelish hisoblanadi

  3. Keyingi har bir aniqlanish (attendance allaqachon bor)
     → checkout_time DOIM yangilanadi (so'nggi ko'ringan vaqt)
     → work_hours qayta hisoblanadi

  4. Natija: bugun birinchi kirish vaqti + oxirgi chiqish vaqti +
             kunda necha marta ko'ringan + barcha aniqlanish vaqtlari
"""
import logging
from datetime import datetime, date, time
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.models.attendance import Attendance, AttendanceStatus, DetectionLog
from backend.models.employee   import Employee
from backend.core.config       import settings

log = logging.getLogger(__name__)


class AttendanceService:

    async def process_recognition(
        self,
        db:            AsyncSession,
        employee_id:   str,
        camera_id:     str,
        snapshot_path: str,
    ) -> Optional[dict]:
        """
        Yuz tanilganda chaqiriladi.
        Har safar DetectionLog yoziladi.
        Attendance esa kuniga faqat 1 ta — birinchi va oxirgi vaqtlar saqlanadi.
        """
        now   = datetime.now()
        today = now.date()

        emp = await db.get(Employee, employee_id)
        if not emp or not emp.is_active:
            return None

        # ── Bugungi davomat yozuvini topish ───────────────────────────────────
        stmt = select(Attendance).where(
            Attendance.employee_id == employee_id,
            Attendance.date        == today,
        )
        result     = await db.execute(stmt)
        attendance = result.scalar_one_or_none()

        action = None

        # ── BIRINCHI ANIQLANISH → checkin ─────────────────────────────────────
        if attendance is None:
            sched_in = self._parse_time(emp.checkin_time or settings.DEFAULT_CHECKIN_TIME)
            late_min = self._minutes_diff(sched_in, now.time())
            status   = (
                AttendanceStatus.late
                if late_min > settings.LATE_THRESHOLD_MINUTES
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
                detection_count   = 0,   # log yozilganda +1 bo'ladi
            )
            db.add(attendance)
            await db.flush()   # id hosil bo'lishi uchun
            action = "checkin"
            log.info(f"[ATT] CHECKIN  {emp.full_name}  {now.strftime('%H:%M')}  status={status}")

        # ── TAKRORIY ANIQLANISH → checkout_time yangilash ─────────────────────
        else:
            # checkout_time DOIM oxirgi ko'ringan vaqtga yangilanadi
            sched_out  = self._parse_time(emp.checkout_time or settings.DEFAULT_CHECKOUT_TIME)
            early_min  = self._minutes_diff(now.time(), sched_out)
            work_hours = round(
                (now - attendance.checkin_time).total_seconds() / 3600, 2
            )
            attendance.checkout_time     = now
            attendance.checkout_image    = snapshot_path
            attendance.work_hours        = work_hours
            attendance.early_out_minutes = max(0, early_min)

            action = "update"
            log.debug(
                f"[ATT] UPDATE   {emp.full_name}  {now.strftime('%H:%M')}  "
                f"hours={work_hours:.2f}  count={attendance.detection_count + 1}"
            )

        # ── DETECTION LOG — har safar yoziladi ────────────────────────────────
        attendance.detection_count = (attendance.detection_count or 0) + 1

        det_log = DetectionLog(
            employee_id   = employee_id,
            attendance_id = attendance.id,
            camera_id     = camera_id,
            detected_at   = now,
            snapshot_path = snapshot_path,
        )
        db.add(det_log)
        await db.commit()

        return {
            "action":           action,
            "employee_id":      employee_id,
            "name":             emp.full_name,
            "position":         emp.position,
            "time":             now.isoformat(),
            "status":           attendance.status,
            "work_hours":       attendance.work_hours,
            "detection_count":  attendance.detection_count,
        }

    # ── Statistika ─────────────────────────────────────────────────────────────

    async def today_stats(self, db: AsyncSession) -> dict:
        today = date.today()

        total_emp = await db.scalar(
            select(func.count()).where(Employee.is_active == True)
        ) or 0
        present = await db.scalar(
            select(func.count()).where(
                Attendance.date == today,
                Attendance.checkin_time.isnot(None),
            )
        ) or 0
        late = await db.scalar(
            select(func.count()).where(
                Attendance.date == today,
                Attendance.status == AttendanceStatus.late,
            )
        ) or 0
        checked_out = await db.scalar(
            select(func.count()).where(
                Attendance.date == today,
                Attendance.checkout_time.isnot(None),
            )
        ) or 0
        # Bugun jami necha marta aniqlanganlar soni
        total_detections = await db.scalar(
            select(func.count()).where(
                DetectionLog.detected_at >= datetime.combine(today, time.min),
            )
        ) or 0

        return {
            "date":             today.isoformat(),
            "total":            total_emp,
            "present":          present,
            "absent":           max(0, total_emp - present),
            "late":             late,
            "checked_out":      checked_out,
            "total_detections": total_detections,
        }

    async def today_list(self, db: AsyncSession) -> list[dict]:
        today = date.today()
        stmt  = (
            select(Attendance, Employee)
            .join(Employee, Attendance.employee_id == Employee.id)
            .where(Attendance.date == today)
            .order_by(Attendance.checkin_time.asc())
        )
        rows   = await db.execute(stmt)
        result = []
        for att, emp in rows:
            result.append({
                "id":              att.id,
                "employee_id":     emp.id,
                "name":            emp.full_name,
                "position":        emp.position,
                "department":      emp.department,
                "checkin_time":    att.checkin_time.strftime("%H:%M:%S")  if att.checkin_time  else None,
                "checkout_time":   att.checkout_time.strftime("%H:%M:%S") if att.checkout_time else None,
                "status":          att.status,
                "late_minutes":    att.late_minutes,
                "work_hours":      att.work_hours,
                "detection_count": att.detection_count,
                "checkin_image":   att.checkin_image,
                "checkout_image":  att.checkout_image,
            })
        return result

    async def detection_logs(
        self,
        db:          AsyncSession,
        employee_id: Optional[str] = None,
        date_str:    Optional[str] = None,
    ) -> list[dict]:
        """Xodimning kun bo'yi barcha aniqlanish vaqtlari."""
        target_date = date.fromisoformat(date_str) if date_str else date.today()

        stmt = (
            select(DetectionLog, Employee)
            .join(Employee, DetectionLog.employee_id == Employee.id)
            .where(
                DetectionLog.detected_at >= datetime.combine(target_date, time.min),
                DetectionLog.detected_at <= datetime.combine(target_date, time.max),
            )
        )
        if employee_id:
            stmt = stmt.where(DetectionLog.employee_id == employee_id)

        stmt = stmt.order_by(DetectionLog.detected_at.asc())
        rows = await db.execute(stmt)

        return [
            {
                "id":           log_row.id,
                "employee_id":  emp.id,
                "name":         emp.full_name,
                "position":     emp.position,
                "camera_id":    log_row.camera_id,
                "detected_at":  log_row.detected_at.strftime("%H:%M:%S"),
                "snapshot":     log_row.snapshot_path,
            }
            for log_row, emp in rows
        ]

    # ── Tarix ──────────────────────────────────────────────────────────────────

    async def history(
        self,
        db:          AsyncSession,
        employee_id: Optional[str] = None,
        date_from:   Optional[str] = None,
        date_to:     Optional[str] = None,
    ) -> list[dict]:
        stmt = select(Attendance, Employee).join(Employee)

        if employee_id:
            stmt = stmt.where(Attendance.employee_id == employee_id)
        if date_from:
            stmt = stmt.where(Attendance.date >= date.fromisoformat(date_from))
        if date_to:
            stmt = stmt.where(Attendance.date <= date.fromisoformat(date_to))

        stmt = stmt.order_by(Attendance.date.desc(), Attendance.checkin_time.desc())
        rows = await db.execute(stmt)

        return [
            {
                "id":              att.id,
                "employee_id":     emp.id,
                "name":            emp.full_name,
                "position":        emp.position,
                "department":      emp.department,
                "date":            att.date.isoformat(),
                "checkin_time":    att.checkin_time.strftime("%H:%M:%S")  if att.checkin_time  else None,
                "checkout_time":   att.checkout_time.strftime("%H:%M:%S") if att.checkout_time else None,
                "status":          att.status,
                "late_minutes":    att.late_minutes,
                "work_hours":      att.work_hours,
                "detection_count": att.detection_count,
            }
            for att, emp in rows
        ]

    # ── Yordamchi ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_time(t: str) -> time:
        h, m = map(int, t.split(":"))
        return time(h, m)

    @staticmethod
    def _minutes_diff(a: time, b: time) -> float:
        return (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)


attendance_service = AttendanceService()
