"""
Shared FastAPI dependencies. The key rule enforced throughout this file:
a JWT proves WHO someone is, but ownership/permission is always re-checked
against the database on every request — never assumed just because a token
looks valid. See catalog/store ownership checks in routers/stores.py for
where this matters most.
"""
import uuid
from fastapi import Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.core.security import decode_token
from app.models.user import User, AdminUser


async def _bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token")
    return authorization[len("Bearer "):]


async def get_current_user(
    token: str = Depends(_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(token, admin=False)
    if not payload or payload.get("token_type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = await db.get(User, uuid.UUID(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


def require_role(*roles: str):
    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Not permitted for this role")
        return user
    return _check


async def get_current_admin(
    token: str = Depends(_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """
    Verified against ADMIN_JWT_SECRET, a different key from customer/seller
    tokens — a leaked customer JWT secret can never be used to forge an
    admin session, and this dependency will reject any token signed with
    the wrong key outright.
    """
    payload = decode_token(token, admin=True)
    if not payload or payload.get("token_type") != "admin_access":
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")
    admin = await db.get(AdminUser, uuid.UUID(payload["sub"]))
    if not admin or not admin.is_active:
        raise HTTPException(status_code=401, detail="Admin account no longer active")
    return admin
