from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from backend.core.database    import get_db
from backend.models.employee  import Employee
from backend.services.face_service import face_service

router = APIRouter(prefix="/employees", tags=["employees"])


class EmployeeCreate(BaseModel):
    full_name:     str
    position:      str = ""
    department:    str = ""
    phone:         str = ""
    checkin_time:  str = "09:00"
    checkout_time: str = "18:00"
    work_days:     str = "1,2,3,4,5"

class EmployeeUpdate(BaseModel):
    full_name:     Optional[str] = None
    position:      Optional[str] = None
    department:    Optional[str] = None
    phone:         Optional[str] = None
    checkin_time:  Optional[str] = None
    checkout_time: Optional[str] = None
    work_days:     Optional[str] = None
    is_active:     Optional[bool] = None


def _emp_dict(e: Employee) -> dict:
    return {
        "id":            e.id,
        "full_name":     e.full_name,
        "position":      e.position,
        "department":    e.department,
        "phone":         e.phone,
        "is_active":     e.is_active,
        "checkin_time":  e.checkin_time,
        "checkout_time": e.checkout_time,
        "work_days":     e.work_days,
        "face_enrolled": e.face_enrolled,
        "created_at":    e.created_at.isoformat() if e.created_at else None,
    }


@router.get("")
async def list_employees(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Employee).order_by(Employee.full_name))
    return {"employees": [_emp_dict(e) for e in rows.scalars()]}


@router.post("", status_code=201)
async def create_employee(body: EmployeeCreate, db: AsyncSession = Depends(get_db)):
    emp = Employee(**body.model_dump())
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    return _emp_dict(emp)


@router.get("/{emp_id}")
async def get_employee(emp_id: str, db: AsyncSession = Depends(get_db)):
    emp = await db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    return _emp_dict(emp)


@router.patch("/{emp_id}")
async def update_employee(
    emp_id: str,
    body: EmployeeUpdate,
    db: AsyncSession = Depends(get_db),
):
    emp = await db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(emp, field, val)
    emp.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(emp)
    return _emp_dict(emp)


@router.delete("/{emp_id}")
async def delete_employee(emp_id: str, db: AsyncSession = Depends(get_db)):
    emp = await db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    await db.delete(emp)
    await db.commit()
    return {"deleted": True}


@router.post("/{emp_id}/enroll")
async def enroll_face(
    emp_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Xodim yuzini tizimga kiritish (bir marta suratga olish)."""
    emp = await db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    image_bytes = await file.read()
    ok = face_service.enroll_employee(emp_id, image_bytes)

    if not ok:
        raise HTTPException(400, "Rasmda yuz topilmadi. Boshqa rasm yuboring.")

    emp.face_enrolled      = True
    emp.face_encoding_path = str(face_service._get_enc_path(emp_id) if hasattr(face_service, '_get_enc_path') else "")
    await db.commit()
    return {"enrolled": True, "employee_id": emp_id}
