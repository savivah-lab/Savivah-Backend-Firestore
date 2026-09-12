"""
Run once per new admin account: python scripts/create_admin.py
This is the ONLY way an admin account is ever created — there is no public
registration endpoint for admins, by design.
"""
import asyncio
import sys
import os
import getpass
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.firestore_client import get_db
from app.core.security import hash_password
from app.services.firestore_helpers import new_id, now


async def main():
    full_name = input("Full name: ").strip()
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")

    db = get_db()
    existing = [d async for d in db.collection("admin_users").where("email", "==", email).limit(1).stream()]
    if existing:
        print(f"An admin with email {email} already exists.")
        return

    admin_id = new_id()
    await db.collection("admin_users").document(admin_id).set({
        "full_name": full_name, "email": email, "password_hash": hash_password(password),
        "totp_secret": None, "is_active": True, "created_at": now(),
    })
    print(f"Created admin account: {email}")


if __name__ == "__main__":
    asyncio.run(main())
