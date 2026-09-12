"""
Firestore has no equivalent of a Postgres UNIQUE column constraint. The
standard pattern to simulate one is a small "lookup" document whose ID IS
the unique value (e.g. an email address) — creating it only succeeds if no
such document exists yet, and it's created in the SAME transaction as the
real record, so the two can never get out of sync.

This is the direct replacement for what `UNIQUE` did in schema.sql for
users.email, users.phone_number, users.google_id, stores.slug, and
admin_users.email.
"""
import uuid
from datetime import datetime, timezone
from google.cloud.firestore import AsyncTransaction, ArrayUnion  # noqa: F401 (re-exported for convenience)


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class UniquenessError(Exception):
    """Raised when a value that must be unique (email, phone, slug, ...) is already taken."""
    def __init__(self, field: str):
        self.field = field
        super().__init__(f"{field} is already in use")


async def claim_unique(transaction: AsyncTransaction, db, lookup_collection: str, key: str, target_id: str, field_name: str):
    """
    Within an existing transaction: reserve `key` in `lookup_collection`,
    pointing at `target_id`. Raises UniquenessError if already claimed by a
    different target. Call once per unique field, inside the same
    transaction that creates the real record.
    """
    ref = db.collection(lookup_collection).document(key)
    snap = await ref.get(transaction=transaction)
    if snap.exists and snap.to_dict().get("id") != target_id:
        raise UniquenessError(field_name)
    transaction.set(ref, {"id": target_id})
