import pyotp
from fastapi import APIRouter, Depends, HTTPException
from app.core.firestore_client import get_db
from app.core.security import verify_password, create_access_token, create_refresh_token, decode_token
from app.core.rate_limit import rate_limit
from app.schemas.auth import AdminLoginRequest, AdminRefreshRequest, AdminTokenResponse, AdminOut

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


def _issue(admin: dict) -> AdminTokenResponse:
    claims = {"sub": admin["id"], "email": admin["email"]}
    return AdminTokenResponse(
        accessToken=create_access_token(claims, admin=True),
        refreshToken=create_refresh_token(claims, admin=True),
        admin=AdminOut(id=admin["id"], fullName=admin["full_name"], email=admin["email"]),
    )


@router.post("/login", response_model=AdminTokenResponse, dependencies=[Depends(rate_limit("admin_login", limit=5))])
async def admin_login(body: AdminLoginRequest, db=Depends(get_db)):
    docs = [d async for d in db.collection("admin_users").where("email", "==", body.email).limit(1).stream()]
    if not docs:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    admin = docs[0].to_dict()
    admin["id"] = docs[0].id
    if not admin.get("is_active", True) or not verify_password(body.password, admin.get("password_hash")):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if admin.get("totp_secret"):
        if not body.totpCode:
            raise HTTPException(status_code=401, detail="2FA code required")
        if not pyotp.TOTP(admin["totp_secret"]).verify(body.totpCode, valid_window=1):
            raise HTTPException(status_code=401, detail="Invalid 2FA code")

    return _issue(admin)


@router.post("/refresh", response_model=AdminTokenResponse)
async def admin_refresh(body: AdminRefreshRequest, db=Depends(get_db)):
    payload = decode_token(body.refreshToken, admin=True)
    if not payload or payload.get("token_type") != "admin_refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    snap = await db.collection("admin_users").document(payload["sub"]).get()
    if not snap.exists or not snap.to_dict().get("is_active", True):
        raise HTTPException(status_code=401, detail="Admin account no longer active")
    admin = snap.to_dict()
    admin["id"] = snap.id
    return _issue(admin)
