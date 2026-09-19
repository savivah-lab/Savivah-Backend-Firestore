import asyncio
import os
import secrets
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException
from google.cloud.firestore import async_transactional

from app.core.firestore_client import get_db
from app.core.redis_client import get_redis
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
)
from app.core.rate_limit import rate_limit
from app.services.firestore_helpers import (
    new_id,
    now,
    claim_unique,
    UniquenessError,
)
from app.services.google_auth import verify_google_token
from app.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    GoogleAuthRequest,
    TokenResponse,
    UserOut,
    VerifyEmailRequest,
    ResendVerificationRequest,
    RequestLoginOtpRequest,
    VerifyLoginOtpRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ============================================================
# OTP CONFIGURATION
# ============================================================

OTP_TTL_SECONDS = 10 * 60
OTP_LENGTH = 6


def _generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _verification_key(email: str) -> str:
    return f"auth:email_verification:{email.lower().strip()}"


def _login_otp_key(email: str) -> str:
    return f"auth:login_otp:{email.lower().strip()}"


# ============================================================
# EMAIL
# ============================================================

def _send_email_sync(
    recipient: str,
    subject: str,
    body: str,
) -> None:
    """
    Sends an email through the SMTP server configured in Render.

    Required environment variables:

    SMTP_HOST
    SMTP_PORT
    SMTP_USERNAME
    SMTP_PASSWORD
    SMTP_FROM_EMAIL

    Example:
        SMTP_HOST=smtp.gmail.com
        SMTP_PORT=465
        SMTP_USERNAME=your-email@gmail.com
        SMTP_PASSWORD=your-app-password
        SMTP_FROM_EMAIL=your-email@gmail.com
    """

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM_EMAIL") or smtp_username

    if not all([
        smtp_host,
        smtp_username,
        smtp_password,
        smtp_from,
    ]):
        raise RuntimeError("SMTP email configuration is incomplete")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = recipient
    message.set_content(body)

    if smtp_port == 465:
        with smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            timeout=20,
        ) as server:
            server.login(
                smtp_username,
                smtp_password,
            )
            server.send_message(message)
    else:
        with smtplib.SMTP(
            smtp_host,
            smtp_port,
            timeout=20,
        ) as server:
            server.starttls()
            server.login(
                smtp_username,
                smtp_password,
            )
            server.send_message(message)


async def _send_email(
    recipient: str,
    subject: str,
    body: str,
) -> None:
    await asyncio.to_thread(
        _send_email_sync,
        recipient,
        subject,
        body,
    )


async def _send_verification_code(
    email: str,
    full_name: str,
    redis,
) -> None:
    code = _generate_otp()

    await redis.set(
        _verification_key(email),
        code,
        ex=OTP_TTL_SECONDS,
    )

    await _send_email(
        email,
        "Verify your Savivah account",
        (
            f"Hello {full_name},\n\n"
            f"Your Savivah verification code is:\n\n"
            f"{code}\n\n"
            f"This code expires in 10 minutes.\n\n"
            f"If you did not create a Savivah account, "
            f"you can ignore this email.\n\n"
            f"Savivah Marketplace"
        ),
    )


async def _send_login_code(
    email: str,
    full_name: str,
    redis,
) -> None:
    code = _generate_otp()

    await redis.set(
        _login_otp_key(email),
        code,
        ex=OTP_TTL_SECONDS,
    )

    await _send_email(
        email,
        "Your Savivah login code",
        (
            f"Hello {full_name},\n\n"
            f"Your Savivah login code is:\n\n"
            f"{code}\n\n"
            f"This code expires in 10 minutes and can only "
            f"be used once.\n\n"
            f"If you did not attempt to log in, "
            f"please secure your account.\n\n"
            f"Savivah Marketplace"
        ),
    )


# ============================================================
# USER RESPONSE
# ============================================================

def _user_out(user: dict) -> UserOut:
    return UserOut(
        id=user["id"],
        fullName=user["full_name"],
        email=user["email"],
        role=user.get("role", "customer"),
        avatarUrl=user.get("avatar_url"),
        emailVerified=bool(user.get("email_verified", False)),
        sellerStatus=user.get("seller_status", "none"),
    )


def _issue(user: dict) -> TokenResponse:
    token = create_access_token(
        {
            "sub": user["id"],
            "role": user.get("role", "customer"),
            "email": user["email"],
        }
    )

    return TokenResponse(
        token=token,
        user=_user_out(user),
    )


# ============================================================
# REGISTER
# ============================================================

@router.post(
    "/register",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("register"))],
)
async def register(
    body: RegisterRequest,
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Public registration always creates a CUSTOMER.

    A customer cannot become a seller simply by sending:
        role=seller

    Seller activation happens through the seller application flow.
    """

    uid = new_id()

    user_doc = {
        "full_name": body.fullName,
        "email": body.email,
        "phone_number": body.phoneNumber,
        "password_hash": hash_password(body.password),

        # IMPORTANT:
        # Public registration can never directly create a seller.
        "role": "customer",

        "national_id": None,
        "kra_pin": None,
        "google_id": None,
        "avatar_url": None,

        # Email verification
        "email_verified": False,

        # Seller application state
        "seller_status": "none",

        "created_at": now(),
    }

    @async_transactional
    async def create_user(transaction):
        email_ref = await claim_unique(
            transaction,
            db,
            "user_emails",
            body.email,
            uid,
            "email",
        )

        phone_ref = await claim_unique(
            transaction,
            db,
            "user_phones",
            body.phoneNumber,
            uid,
            "phone number",
        )

        # All reads complete before writes.
        transaction.set(
            email_ref,
            {"id": uid},
        )

        transaction.set(
            phone_ref,
            {"id": uid},
        )

        transaction.set(
            db.collection("users").document(uid),
            user_doc,
        )

    try:
        await create_user(db.transaction())
    except UniquenessError:
        raise HTTPException(
            status_code=409,
            detail="Email or phone already registered",
        )

    user = {
        **user_doc,
        "id": uid,
    }

    # Send verification code.
    try:
        await _send_verification_code(
            body.email,
            body.fullName,
            redis,
        )
    except Exception:
        # Account exists, but verification email failed.
        # Do not delete the account automatically.
        pass

    return _issue(user)


# ============================================================
# VERIFY EMAIL
# ============================================================

@router.post(
    "/verify-email",
    response_model=TokenResponse,
)
async def verify_email(
    body: VerifyEmailRequest,
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    email = body.email.lower().strip()

    stored_code = await redis.get(
        _verification_key(email)
    )

    if not stored_code:
        raise HTTPException(
            status_code=400,
            detail="Verification code expired or not found",
        )

    if isinstance(stored_code, bytes):
        stored_code = stored_code.decode()

    if stored_code != body.code:
        raise HTTPException(
            status_code=400,
            detail="Invalid verification code",
        )

    docs = [
        d
        async for d in (
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
    ]

    if not docs:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    doc = docs[0]
    user = doc.to_dict()
    user["id"] = doc.id

    await (
        db.collection("users")
        .document(doc.id)
        .update({
            "email_verified": True,
        })
    )

    # OTP is one-time.
    await redis.delete(
        _verification_key(email)
    )

    user["email_verified"] = True

    return _issue(user)


# ============================================================
# RESEND EMAIL VERIFICATION
# ============================================================

@router.post(
    "/resend-verification",
    dependencies=[Depends(rate_limit("resend_verification"))],
)
async def resend_verification(
    body: ResendVerificationRequest,
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    email = body.email.lower().strip()

    docs = [
        d
        async for d in (
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
    ]

    # Do not reveal whether an email exists.
    if not docs:
        return {
            "message": "If the account exists, a verification code has been sent."
        }

    doc = docs[0]
    user = doc.to_dict()

    if user.get("email_verified"):
        return {
            "message": "Email is already verified."
        }

    try:
        await _send_verification_code(
            email,
            user.get("full_name", "there"),
            redis,
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Could not send verification email",
        )

    return {
        "message": "Verification code sent.",
    }


# ============================================================
# PASSWORD LOGIN
# ============================================================

@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("login"))],
)
async def login(
    body: LoginRequest,
    db=Depends(get_db),
):
    email = body.email.lower().strip()

    query = (
        db.collection("users")
        .where("email", "==", email)
        .limit(1)
    )

    docs = [d async for d in query.stream()]

    if not docs:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    user = docs[0].to_dict()
    user["id"] = docs[0].id

    if not verify_password(
        body.password,
        user.get("password_hash"),
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    # Require verified email before normal login.
    if not user.get("email_verified", False):
        raise HTTPException(
            status_code=403,
            detail="Please verify your email before logging in",
        )

    return _issue(user)


# ============================================================
# REQUEST LOGIN OTP
# ============================================================

@router.post(
    "/request-login-otp",
    dependencies=[Depends(rate_limit("login_otp"))],
)
async def request_login_otp(
    body: RequestLoginOtpRequest,
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    email = body.email.lower().strip()

    docs = [
        d
        async for d in (
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
    ]

    # Don't reveal whether the account exists.
    if not docs:
        return {
            "message": "If the account exists, a login code has been sent."
        }

    doc = docs[0]
    user = doc.to_dict()

    if not user.get("email_verified", False):
        raise HTTPException(
            status_code=403,
            detail="Please verify your email before requesting a login code",
        )

    try:
        await _send_login_code(
            email,
            user.get("full_name", "there"),
            redis,
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Could not send login code",
        )

    return {
        "message": "Login code sent.",
    }


# ============================================================
# VERIFY LOGIN OTP
# ============================================================

@router.post(
    "/verify-login-otp",
    response_model=TokenResponse,
)
async def verify_login_otp(
    body: VerifyLoginOtpRequest,
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    email = body.email.lower().strip()

    stored_code = await redis.get(
        _login_otp_key(email)
    )

    if not stored_code:
        raise HTTPException(
            status_code=400,
            detail="Login code expired or not found",
        )

    if isinstance(stored_code, bytes):
        stored_code = stored_code.decode()

    if stored_code != body.code:
        raise HTTPException(
            status_code=400,
            detail="Invalid login code",
        )

    docs = [
        d
        async for d in (
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
    ]

    if not docs:
        raise HTTPException(
            status_code=401,
            detail="Invalid login code",
        )

    doc = docs[0]
    user = doc.to_dict()
    user["id"] = doc.id

    if not user.get("email_verified", False):
        raise HTTPException(
            status_code=403,
            detail="Email is not verified",
        )

    # OTP is one-time.
    await redis.delete(
        _login_otp_key(email)
    )

    return _issue(user)


# ============================================================
# GOOGLE AUTH
# ============================================================

@router.post(
    "/google",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("google_auth"))],
)
async def google_auth(
    body: GoogleAuthRequest,
    db=Depends(get_db),
):
    try:
        payload = verify_google_token(body.idToken)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Google sign-in failed",
        )

    if not payload.get("email_verified"):
        raise HTTPException(
            status_code=401,
            detail="Google account email is not verified",
        )

    email = payload["email"].lower().strip()

    by_google = [
        d
        async for d in (
            db.collection("users")
            .where("google_id", "==", payload["sub"])
            .limit(1)
            .stream()
        )
    ]

    by_email = (
        []
        if by_google
        else [
            d
            async for d in (
                db.collection("users")
                .where("email", "==", email)
                .limit(1)
                .stream()
            )
        ]
    )

    existing = (by_google or by_email or [None])[0]

    if existing is None:
        uid = new_id()

        user_doc = {
            "full_name": payload.get(
                "name",
                email,
            ),
            "email": email,
            "phone_number": None,
            "password_hash": None,

            # Google signup also starts as customer.
            "role": "customer",

            "national_id": None,
            "kra_pin": None,
            "google_id": payload["sub"],
            "avatar_url": payload.get("picture"),

            # Google verified the email.
            "email_verified": True,

            "seller_status": "none",

            "created_at": now(),
        }

        @async_transactional
        async def create_google_user(transaction):
            email_ref = await claim_unique(
                transaction,
                db,
                "user_emails",
                email,
                uid,
                "email",
            )

            # IMPORTANT:
            # claim_unique returns the reference.
            # We must actually write that reference.
            transaction.set(
                email_ref,
                {"id": uid},
            )

            transaction.set(
                db.collection("users").document(uid),
                user_doc,
            )

        try:
            await create_google_user(
                db.transaction()
            )
        except UniquenessError:
            raise HTTPException(
                status_code=409,
                detail="Email already registered",
            )

        user = {
            **user_doc,
            "id": uid,
        }

    else:
        user = existing.to_dict()
        user["id"] = existing.id

        updates = {}

        if not user.get("google_id"):
            updates["google_id"] = payload["sub"]

        if not user.get("avatar_url") and payload.get("picture"):
            updates["avatar_url"] = payload["picture"]

        # Google has verified this email.
        if not user.get("email_verified"):
            updates["email_verified"] = True

        if updates:
            await (
                db.collection("users")
                .document(user["id"])
                .update(updates)
            )

            user.update(updates)

    return _issue(user)
