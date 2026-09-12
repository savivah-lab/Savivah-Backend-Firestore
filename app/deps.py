"""
Same rule as before: a JWT proves WHO someone is, but every request still
re-reads the current record from Firestore rather than trusting anything
beyond identity out of the token — see stores.py for where this matters
most (store/product ownership).
"""
from fastapi import Depends, HTTPException, Header
from app.core.firestore_client import get_db
from app.core.security import decode_token


async def _bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token")
    return authorization[len("Bearer "):]


async def get_current_user(token: str = Depends(_bearer_token), db=Depends(get_db)) -> dict:
    payload = decode_token(token, admin=False)
    if not payload or payload.get("token_type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    snap = await db.collection("users").document(payload["sub"]).get()
    if not snap.exists:
        raise HTTPException(status_code=401, detail="User no longer exists")
    user = snap.to_dict()
    user["id"] = snap.id
    return user


def require_role(*roles: str):
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Not permitted for this role")
        return user
    return _check


async def get_current_admin(token: str = Depends(_bearer_token), db=Depends(get_db)) -> dict:
    payload = decode_token(token, admin=True)
    if not payload or payload.get("token_type") != "admin_access":
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")
    snap = await db.collection("admin_users").document(payload["sub"]).get()
    if not snap.exists or not snap.to_dict().get("is_active", True):
        raise HTTPException(status_code=401, detail="Admin account no longer active")
    admin = snap.to_dict()
    admin["id"] = snap.id
    return admin
