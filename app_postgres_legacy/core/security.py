"""
Password hashing and JWT issuing/verification. Customer/seller tokens and
admin tokens are signed with DIFFERENT secrets (JWT_SECRET vs
ADMIN_JWT_SECRET) — an admin token can never be forged from a leaked
customer secret, and vice versa. This is the "dedicated signing key" control
from the architecture spec.
"""
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return pwd_context.verify(password, password_hash)


def create_access_token(data: dict, admin: bool = False) -> str:
    secret = settings.ADMIN_JWT_SECRET if admin else settings.JWT_SECRET
    minutes = settings.ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES if admin else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    to_encode = {**data, "exp": expire, "token_type": "admin_access" if admin else "access"}
    return jwt.encode(to_encode, secret, algorithm=ALGORITHM)


def create_refresh_token(data: dict, admin: bool = False) -> str:
    secret = settings.ADMIN_JWT_SECRET if admin else settings.JWT_SECRET
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {**data, "exp": expire, "token_type": "admin_refresh" if admin else "refresh"}
    return jwt.encode(to_encode, secret, algorithm=ALGORITHM)


def decode_token(token: str, admin: bool = False) -> dict | None:
    secret = settings.ADMIN_JWT_SECRET if admin else settings.JWT_SECRET
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM])
    except JWTError:
        return None
