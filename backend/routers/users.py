from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.models.user   import User

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    full_name:    str
    connected_id: str
    note:         Optional[str] = ""


class UserUpdate(BaseModel):
    full_name:    Optional[str] = None
    connected_id: Optional[str] = None
    note:         Optional[str] = None
    is_active:    Optional[bool] = None


def _fmt(u: User) -> dict:
    return {
        "id":           u.id,
        "full_name":    u.full_name,
        "connected_id": u.connected_id,
        "note":         u.note,
        "is_active":    u.is_active,
        "created_at":   u.created_at.isoformat() if u.created_at else None,
    }


@router.get("")
async def list_users(
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User).order_by(User.full_name)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    rows = await db.execute(stmt)
    return {"users": [_fmt(u) for u in rows.scalars()]}


@router.post("", status_code=201)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db)):
    # connected_id takrorlanmasligi
    existing = await db.execute(
        select(User).where(User.connected_id == body.connected_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"connected_id '{body.connected_id}' already exists")

    user = User(**body.model_dump())
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _fmt(user)


@router.get("/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return _fmt(user)


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdate, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    data = body.model_dump(exclude_none=True)

    # connected_id o'zgaryapti — takrorlanmasligini tekshirish
    if "connected_id" in data and data["connected_id"] != user.connected_id:
        existing = await db.execute(
            select(User).where(User.connected_id == data["connected_id"])
        )
        if existing.scalar_one_or_none():
            raise HTTPException(400, f"connected_id '{data['connected_id']}' already exists")

    for field, val in data.items():
        setattr(user, field, val)

    await db.commit()
    await db.refresh(user)
    return _fmt(user)


@router.delete("/{user_id}")
async def delete_user(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    await db.delete(user)
    await db.commit()
    return {"deleted": True}
