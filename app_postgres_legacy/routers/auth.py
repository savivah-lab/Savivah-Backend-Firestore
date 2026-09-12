"""
Customer/seller authentication. Deliberately CANNOT produce an admin
account through any path here — admin is a fully separate table, separate
login, separate router (see admin_auth.py). This closes the gap from the
previous domain-based "@savivah.co.ke = admin" approach, which was flagged
as a real security concern (no email-ownership verification meant it wasn't
a real access control).
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.core.rate_limit import rate_limit
from app.models.user import User
from app.schemas.auth import RegisterRequest, LoginRequest, GoogleAuthRequest, TokenResponse, UserOut
from app.services.google_auth import verify_google_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue(user: User) -> TokenResponse:
    token = create_access_token({"sub": str(user.id), "role": user.role, "email": user.email})
    return TokenResponse(token=token, user=UserOut(
        id=user.id, fullName=user.full_name, email=user.email, role=user.role, avatarUrl=user.avatar_url,
    ))


@router.post("/register", response_model=TokenResponse, dependencies=[Depends(rate_limit("register"))])
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    role = "seller" if body.role == "seller" else "customer"
    user = User(
        full_name=body.fullName, email=body.email, phone_number=body.phoneNumber,
        password_hash=hash_password(body.password), role=role,
    )
    db.add(user)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email or phone already registered")
    await db.refresh(user)
    return _issue(user)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit("login"))])
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return _issue(user)


@router.post("/google", response_model=TokenResponse, dependencies=[Depends(rate_limit("google_auth"))])
async def google_auth(body: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = verify_google_token(body.idToken)
    except ValueError:
        raise HTTPException(status_code=401, detail="Google sign-in failed")
    if not payload.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google account email is not verified")

    user = (await db.execute(
        select(User).where((User.google_id == payload["sub"]) | (User.email == payload["email"]))
    )).scalar_one_or_none()

    if not user:
        role = "seller" if body.role == "seller" else "customer"
        user = User(
            full_name=payload.get("name", payload["email"]), email=payload["email"],
            phone_number=None, password_hash=None, role=role,
            google_id=payload["sub"], avatar_url=payload.get("picture"),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    elif not user.google_id:
        user.google_id = payload["sub"]
        user.avatar_url = user.avatar_url or payload.get("picture")
        await db.commit()

    return _issue(user)
