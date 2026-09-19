from pydantic import BaseModel, EmailStr, Field
import uuid


# =========================================================
# CUSTOMER / USER REGISTRATION
# =========================================================

class RegisterRequest(BaseModel):
    fullName: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phoneNumber: str = Field(min_length=7, max_length=20)
    password: str = Field(min_length=8, max_length=128)

    # Public registration should normally remain customer.
    # Seller activation happens through the seller application flow.
    role: str = "customer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    idToken: str
    role: str = "customer"


# =========================================================
# EMAIL / OTP VERIFICATION
# =========================================================

class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class RequestLoginOtpRequest(BaseModel):
    email: EmailStr


class VerifyLoginOtpRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


# =========================================================
# USER RESPONSE
# =========================================================

class UserOut(BaseModel):
    id: uuid.UUID
    fullName: str
    email: str
    role: str
    avatarUrl: str | None = None

    # New account state
    emailVerified: bool = False
    sellerStatus: str = "none"


class TokenResponse(BaseModel):
    token: str
    user: UserOut


# =========================================================
# SELLER APPLICATION
# =========================================================

class SellerApplicationRequest(BaseModel):
    fullName: str = Field(min_length=2, max_length=100)
    phoneNumber: str = Field(min_length=7, max_length=20)

    identificationType: str
    identificationNumber: str = Field(min_length=3, max_length=100)

    businessName: str = Field(min_length=2, max_length=150)
    businessRegistrationNumber: str | None = None

    email: EmailStr


class SellerApplicationOut(BaseModel):
    id: uuid.UUID
    userId: uuid.UUID

    fullName: str
    email: str
    phoneNumber: str

    identificationType: str

    businessName: str
    businessRegistrationNumber: str | None = None

    feeAmount: float
    currency: str = "KES"

    paymentStatus: str
    verificationStatus: str
    status: str


# =========================================================
# ADMIN AUTH
# =========================================================

class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str
    totpCode: str | None = None


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
