from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.auth     import (
    hash_password, create_token, verify_password,
    get_current_brand, require_superuser,
)
from backend.models.brand  import Brand
from backend.models.camera import Camera

router = APIRouter(tags=["brands"])


# ── Pydantic ──────────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str

class BrandCreate(BaseModel):
    name:     str
    username: str
    password: str

class BrandUpdate(BaseModel):
    name:      Optional[str] = None
    username:  Optional[str] = None
    password:  Optional[str] = None
    is_active: Optional[bool] = None


def _fmt(b: Brand) -> dict:
    return {
        "id":           b.id,
        "name":         b.name,
        "username":     b.username,
        "is_superuser": b.is_superuser,
        "is_active":    b.is_active,
        "created_at":   b.created_at.isoformat() if b.created_at else None,
    }


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.post("/api/auth/login")
async def login(body: LoginBody, db: AsyncSession = Depends(get_db)):
    """Username va parol bilan kirish. JWT token qaytaradi."""
    result = await db.execute(select(Brand).where(Brand.username == body.username))
    brand  = result.scalar_one_or_none()

    if not brand or not brand.is_active or not verify_password(body.password, brand.password_hash):
        raise HTTPException(401, "Username yoki parol noto'g'ri")

    token = create_token(brand.id, brand.is_superuser)
    return {
        "access_token": token,
        "token_type":   "bearer",
        "brand": _fmt(brand),
    }


@router.get("/api/auth/me")
async def me(brand: Brand = Depends(get_current_brand)):
    """Joriy foydalanuvchi ma'lumoti."""
    return _fmt(brand)


# ── Brand CRUD (faqat superuser) ──────────────────────────────────────────────

@router.get("/api/brands")
async def list_brands(brand: Brand = Depends(require_superuser), db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Brand).order_by(Brand.name))
    return {"brands": [_fmt(b) for b in rows.scalars()]}


@router.post("/api/brands", status_code=201)
async def create_brand(
    body:  BrandCreate,
    admin: Brand = Depends(require_superuser),
    db:    AsyncSession = Depends(get_db),
):
    # Name va username takrorlanmasligini tekshirish
    for field, val in [("name", body.name), ("username", body.username)]:
        col  = Brand.name if field == "name" else Brand.username
        row  = (await db.execute(select(Brand).where(col == val))).scalar_one_or_none()
        if row:
            raise HTTPException(400, f"'{val}' allaqachon mavjud")

    brand = Brand(
        name          = body.name,
        username      = body.username,
        password_hash = hash_password(body.password),
        is_superuser  = False,
    )
    db.add(brand)
    await db.commit()
    await db.refresh(brand)
    return _fmt(brand)


@router.patch("/api/brands/{brand_id}")
async def update_brand(
    brand_id: str,
    body:     BrandUpdate,
    admin:    Brand = Depends(require_superuser),
    db:       AsyncSession = Depends(get_db),
):
    brand = await db.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "Brand topilmadi")
    if brand.is_superuser:
        raise HTTPException(403, "Superuserni o'zgartirish mumkin emas")

    data = body.model_dump(exclude_none=True)
    if "password" in data:
        brand.password_hash = hash_password(data.pop("password"))
    for k, v in data.items():
        setattr(brand, k, v)

    await db.commit()
    await db.refresh(brand)
    return _fmt(brand)


@router.delete("/api/brands/{brand_id}")
async def delete_brand(
    brand_id: str,
    admin:    Brand = Depends(require_superuser),
    db:       AsyncSession = Depends(get_db),
):
    brand = await db.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "Brand topilmadi")
    if brand.is_superuser:
        raise HTTPException(403, "Superuserni o'chirish mumkin emas")
    # Kameralarni brand'dan uzish (o'chirmaslik)
    await db.execute(
        Camera.__table__.update()
        .where(Camera.brand_id == brand_id)
        .values(brand_id=None)
    )
    await db.delete(brand)
    await db.commit()
    return {"deleted": True}


# ── O'z profilini yangilash (istalgan brand) ──────────────────────────────────

@router.patch("/api/auth/password")
async def change_password(
    body:  LoginBody,   # username + new_password
    brand: Brand = Depends(get_current_brand),
    db:    AsyncSession = Depends(get_db),
):
    """Faqat o'z parolini o'zgartirish."""
    brand.password_hash = hash_password(body.password)
    await db.commit()
    return {"updated": True}
