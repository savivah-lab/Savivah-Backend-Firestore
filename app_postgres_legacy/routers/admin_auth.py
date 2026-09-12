"""
Admin authentication, entirely separate from customer/seller auth per the
architecture spec: its own signing key (ADMIN_JWT_SECRET), short-lived
access tokens, a refresh flow, and a 2FA hook. There is deliberately no
POST /register here — admin accounts are provisioned directly in the
database by a developer/ops person (see the README for the exact command).
"""
import pyotp
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.core.security import verify_password, create_access_token, create_refresh_token, decode_token
from app.core.rate_limit import rate_limit
from app.models.user import AdminUser
from app.schemas.auth import AdminLoginRequest, AdminRefreshRequest, AdminTokenResponse, AdminOut

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


def _issue(admin: AdminUser) -> AdminTokenResponse:
    claims = {"sub": str(admin.id), "email": admin.email}
    return AdminTokenResponse(
        accessToken=create_access_token(claims, admin=True),
        refreshToken=create_refresh_token(claims, admin=True),
        admin=AdminOut(id=admin.id, fullName=admin.full_name, email=admin.email),
    )


@router.post("/login", response_model=AdminTokenResponse, dependencies=[Depends(rate_limit("admin_login", limit=5))])
async def admin_login(body: AdminLoginRequest, db: AsyncSession = Depends(get_db)):
    admin = (await db.execute(
        select(AdminUser).where(AdminUser.email == body.email, AdminUser.is_active == True)  # noqa: E712
    )).scalar_one_or_none()
    if not admin or not verify_password(body.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if admin.totp_secret:
        if not body.totpCode:
            raise HTTPException(status_code=401, detail="2FA code required")
        if not pyotp.TOTP(admin.totp_secret).verify(body.totpCode, valid_window=1):
            raise HTTPException(status_code=401, detail="Invalid 2FA code")

    return _issue(admin)


@router.post("/refresh", response_model=AdminTokenResponse)
async def admin_refresh(body: AdminRefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.refreshToken, admin=True)
    if not payload or payload.get("token_type") != "admin_refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    import uuid as _uuid
    admin = await db.get(AdminUser, _uuid.UUID(payload["sub"]))
    if not admin or not admin.is_active:
        raise HTTPException(status_code=401, detail="Admin account no longer active")
    return _issue(admin)
