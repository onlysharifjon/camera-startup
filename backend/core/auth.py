"""
JWT Authentication
------------------
Token olish:   POST /api/auth/login  {username, password}
Token tekshirish: Authorization: Bearer <token>

Superuser hamma narsani ko'radi.
Brand foydalanuvchisi faqat o'z kameralarini ko'radi.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.models.brand  import Brand

# ── Kalitlar ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "military-cam-secret-2026-change-in-prod")
ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS = 24

pwd_ctx  = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer   = HTTPBearer(auto_error=False)


# ── Parol ─────────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return pwd_ctx.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


# ── Token ─────────────────────────────────────────────────────────────────────

def create_token(brand_id: str, is_superuser: bool) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": brand_id, "su": is_superuser, "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM,
    )

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")


# ── Depends ───────────────────────────────────────────────────────────────────

async def get_current_brand(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db:    AsyncSession = Depends(get_db),
) -> Brand:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    payload = decode_token(creds.credentials)
    brand   = await db.get(Brand, payload["sub"])
    if not brand or not brand.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Brand not found or inactive")
    return brand


async def require_superuser(brand: Brand = Depends(get_current_brand)) -> Brand:
    if not brand.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser required")
    return brand


def optional_auth(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> Optional[dict]:
    """Token bo'lsa decode qiladi, bo'lmasa None qaytaradi."""
    if not creds:
        return None
    try:
        return decode_token(creds.credentials)
    except HTTPException:
        return None
