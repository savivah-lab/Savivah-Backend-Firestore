import uuid
from datetime import datetime, timezone
from google.cloud.firestore import AsyncTransaction, ArrayUnion  # noqa: F401


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class UniquenessError(Exception):
    """Raised when a value that must be unique is already taken."""
    def __init__(self, field: str):
        self.field = field
        super().__init__(f"{field} is already in use")


async def claim_unique(
    transaction: AsyncTransaction,
    db,
    lookup_collection: str,
    key: str,
    target_id: str,
    field_name: str,
):
    """
    Check whether `key` is already claimed.

    IMPORTANT:
    This function only READS. It does not write to the transaction.
    All transaction reads must happen before transaction writes.
    """
    ref = db.collection(lookup_collection).document(key)
    snap = await ref.get(transaction=transaction)

    if snap.exists and snap.to_dict().get("id") != target_id:
        raise UniquenessError(field_name)

    return ref
