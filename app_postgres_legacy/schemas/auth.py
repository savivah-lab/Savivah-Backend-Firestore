from pydantic import BaseModel, EmailStr
import uuid


class RegisterRequest(BaseModel):
    fullName: str
    email: EmailStr
    phoneNumber: str
    password: str
    role: str = "customer"  # "customer" | "seller" — never "admin"; see AdminUser


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    idToken: str
    role: str = "customer"


class UserOut(BaseModel):
    id: uuid.UUID
    fullName: str
    email: str
    role: str
    avatarUrl: str | None = None


class TokenResponse(BaseModel):
    token: str
    user: UserOut


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str
    totpCode: str | None = None  # required once 2FA is enabled for the admin account


class AdminRefreshRequest(BaseModel):
    refreshToken: str


class AdminOut(BaseModel):
    id: uuid.UUID
    fullName: str
    email: str


class AdminTokenResponse(BaseModel):
    accessToken: str
    refreshToken: str
    admin: AdminOut
