from fastapi import APIRouter, Depends, HTTPException
from google.cloud.firestore import async_transactional
from app.core.firestore_client import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.core.rate_limit import rate_limit
from app.services.firestore_helpers import new_id, now, claim_unique, UniquenessError
from app.services.google_auth import verify_google_token
from app.schemas.auth import RegisterRequest, LoginRequest, GoogleAuthRequest, TokenResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue(user: dict) -> TokenResponse:
    token = create_access_token({"sub": user["id"], "role": user["role"], "email": user["email"]})
    return TokenResponse(token=token, user=UserOut(
        id=user["id"], fullName=user["full_name"], email=user["email"],
        role=user["role"], avatarUrl=user.get("avatar_url"),
    ))


@router.post("/register", response_model=TokenResponse, dependencies=[Depends(rate_limit("register"))])
async def register(body: RegisterRequest, db=Depends(get_db)):
    role = "seller" if body.role == "seller" else "customer"
    uid = new_id()
    user_doc = {
        "full_name": body.fullName, "email": body.email, "phone_number": body.phoneNumber,
        "password_hash": hash_password(body.password), "role": role,
        "national_id": None, "kra_pin": None, "google_id": None, "avatar_url": None,
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

    # All reads are complete. Now perform the writes.
    transaction.set(email_ref, {"id": uid})
    transaction.set(phone_ref, {"id": uid})
    transaction.set(
        db.collection("users").document(uid),
        user_doc,
    )


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit("login"))])
async def login(body: LoginRequest, db=Depends(get_db)):
    query = db.collection("users").where("email", "==", body.email).limit(1)
    docs = [d async for d in query.stream()]
    if not docs:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user = docs[0].to_dict()
    user["id"] = docs[0].id
    if not verify_password(body.password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return _issue(user)


@router.post("/google", response_model=TokenResponse, dependencies=[Depends(rate_limit("google_auth"))])
async def google_auth(body: GoogleAuthRequest, db=Depends(get_db)):
    try:
        payload = verify_google_token(body.idToken)
    except ValueError:
        raise HTTPException(status_code=401, detail="Google sign-in failed")
    if not payload.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google account email is not verified")

    by_google = [d async for d in db.collection("users").where("google_id", "==", payload["sub"]).limit(1).stream()]
    by_email = [] if by_google else [d async for d in db.collection("users").where("email", "==", payload["email"]).limit(1).stream()]
    existing = (by_google or by_email or [None])[0]

    if existing is None:
        role = "seller" if body.role == "seller" else "customer"
        uid = new_id()
        user_doc = {
            "full_name": payload.get("name", payload["email"]), "email": payload["email"],
            "phone_number": None, "password_hash": None, "role": role,
            "national_id": None, "kra_pin": None, "google_id": payload["sub"],
            "avatar_url": payload.get("picture"), "created_at": now(),
        }

        @async_transactional
        async def create_google_user(transaction):
            await claim_unique(transaction, db, "user_emails", payload["email"], uid, "email")
            transaction.set(db.collection("users").document(uid), user_doc)

        try:
            await create_google_user(db.transaction())
        except UniquenessError:
            raise HTTPException(status_code=409, detail="Email already registered")
        user = {**user_doc, "id": uid}
    else:
        user = existing.to_dict()
        user["id"] = existing.id
        if not user.get("google_id"):
            await db.collection("users").document(user["id"]).update({
                "google_id": payload["sub"],
                "avatar_url": user.get("avatar_url") or payload.get("picture"),
            })
            user["google_id"] = payload["sub"]

    return _issue(user)
