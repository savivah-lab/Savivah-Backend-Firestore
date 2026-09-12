"""
Two real differences from the Postgres version, worth knowing about:

1. Firestore has no ILIKE/substring search. `search` here matches on a
   PREFIX of the product name only (via a stored lowercase `name_lower`
   field and a range query), not "contains anywhere" like the old
   `ILIKE '%term%'` did. Good enough for a lot of real use, but it's a
   genuine capability loss — if full substring/fuzzy search matters, the
   standard Firestore answer is a dedicated search service (Algolia,
   Typesense, Meilisearch) synced via a write trigger, not built into
   Firestore itself.

2. Firestore has no JOINs. `store_name`/`store_verified` are denormalized
   directly onto each product document at write time (see stores.py).
   That means if a seller renames their store, already-listed products
   keep showing the OLD name until they're next edited — a real trade-off,
   not a bug. Fixing it properly means a Cloud Function that fans the
   rename out to every product on store update, which isn't set up here.

3. Cursor-based pagination only works on the unfiltered/browse view. A
   search query is ordered by `name_lower` instead of `created_at` (a
   different Firestore index), so a cursor from a browse page can't be
   reused mid-search — the frontend just gets every matching result in one
   page when searching, which is fine at small-to-medium catalogue sizes
   but is a real simplification worth knowing about.
"""
import base64
from fastapi import APIRouter, Depends, Query
from app.core.firestore_client import get_db
from app.core.redis_client import get_redis
from app.schemas.product import ProductOut, ProductPage
from app.services.cache import get_cached_products, set_cached_products

router = APIRouter(prefix="/api/products", tags=["products"])

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 60


def _encode_cursor(created_at_iso: str, doc_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at_iso}|{doc_id}".encode()).decode()


def _decode_cursor(cursor: str):
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    created_at_iso, doc_id = raw.split("|", 1)
    return created_at_iso, doc_id


@router.get("", response_model=ProductPage)
async def list_products(
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, le=MAX_PAGE_SIZE),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    cached = await get_cached_products(redis, search, category, cursor, limit)
    if cached:
        return ProductPage(**cached)

    query = db.collection("products").where("status", "==", "active")

    if search:
        term = search.lower()
        # Prefix-match trick: name_lower >= "term" and < "term" + highest
        # unicode char catches every string that starts with `term`.
        query = query.where("name_lower", ">=", term).where("name_lower", "<", term + "\uf8ff")
        query = query.order_by("name_lower")
    else:
        query = query.order_by("created_at", direction="DESCENDING").order_by("__name__", direction="DESCENDING")

    if category:
        query = query.where("category", "==", category)

    if cursor and not search:
        created_at_iso, doc_id = _decode_cursor(cursor)
        from datetime import datetime
        query = query.start_after({"created_at": datetime.fromisoformat(created_at_iso), "__name__": db.collection("products").document(doc_id)})

    query = query.limit(limit + 1)  # one extra to know if there's a next page
    docs = [d async for d in query.stream()]
    has_more = len(docs) > limit
    docs = docs[:limit]

    items = []
    for d in docs:
        p = d.to_dict()
        items.append(ProductOut(
            id=d.id, store_id=p["store_id"], store_name=p.get("store_name"), store_verified=p.get("store_verified"),
            name=p["name"], description=p.get("description"), category=p.get("category"),
            price=float(p["price"]), stock=p["stock"], image_url=p.get("image_url"), status=p["status"],
        ))

    next_cursor = None
    if has_more and docs and not search:
        last = docs[-1].to_dict()
        next_cursor = _encode_cursor(last["created_at"].isoformat(), docs[-1].id)

    page = ProductPage(items=items, next_cursor=next_cursor)
    await set_cached_products(redis, search, category, cursor, limit, page.model_dump(mode="json"))
    return page
