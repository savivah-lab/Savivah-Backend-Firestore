"""
Public product catalogue.

Firestore provides:
- prefix search through name_lower
- category filtering
- cursor pagination for browse mode

Additional catalogue filters:
- min_price
- max_price
- in_stock
- verified_seller
- sort

The existing Redis cache is preserved, with all filter parameters included
in the cache key.
"""

import base64
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.core.firestore_client import get_db
from app.core.redis_client import get_redis
from app.schemas.product import ProductOut, ProductPage
from app.services.cache import get_cached_products, set_cached_products


router = APIRouter(prefix="/api/products", tags=["products"])


DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 60


def _encode_cursor(created_at_iso: str, doc_id: str) -> str:
    return base64.urlsafe_b64encode(
        f"{created_at_iso}|{doc_id}".encode()
    ).decode()


def _decode_cursor(cursor: str):
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    created_at_iso, doc_id = raw.split("|", 1)
    return created_at_iso, doc_id


@router.get("", response_model=ProductPage)
async def list_products(
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),

    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),

    in_stock: bool = Query(default=False),
    verified_seller: bool = Query(default=False),

    sort: str = Query(default="relevance"),

    cursor: str | None = Query(default=None),

    limit: int = Query(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
    ),

    db=Depends(get_db),
    redis=Depends(get_redis),
):
    # ---------------------------------------------------------
    # Normalize inputs
    # ---------------------------------------------------------

    search_value = search.strip() if search else None
    category_value = category.strip() if category else None

    sort_value = (sort or "relevance").lower()

    allowed_sorts = {
        "relevance",
        "newest",
        "price_asc",
        "price_desc",
    }

    if sort_value not in allowed_sorts:
        sort_value = "relevance"

    # Avoid an invalid range.
    if (
        min_price is not None
        and max_price is not None
        and min_price > max_price
    ):
        min_price, max_price = max_price, min_price

    # ---------------------------------------------------------
    # Redis cache
    #
    # IMPORTANT:
    # Every filter is included in the cache key.
    # ---------------------------------------------------------

    cached = await get_cached_products(
        redis,
        search_value,
        category_value,
        cursor,
        limit,
        min_price=min_price,
        max_price=max_price,
        in_stock=in_stock,
        verified_seller=verified_seller,
        sort=sort_value,
    )

    if cached:
        return ProductPage(**cached)

    # ---------------------------------------------------------
    # Base Firestore query
    # ---------------------------------------------------------

    query = db.collection("products").where(
        "status",
        "==",
        "active",
    )

    # ---------------------------------------------------------
    # Search
    #
    # Firestore does prefix search using name_lower.
    # ---------------------------------------------------------

    if search_value:
        term = search_value.lower()

        query = (
            query
            .where("name_lower", ">=", term)
            .where("name_lower", "<", term + "\uf8ff")
            .order_by("name_lower")
        )

    else:
        query = (
            query
            .order_by(
                "created_at",
                direction="DESCENDING",
            )
            .order_by(
                "__name__",
                direction="DESCENDING",
            )
        )

    # ---------------------------------------------------------
    # Category
    # ---------------------------------------------------------

    if category_value:
        query = query.where(
            "category",
            "==",
            category_value,
        )

    # ---------------------------------------------------------
    # Cursor
    #
    # Existing cursor pagination remains available for the
    # normal browse view.
    #
    # Search continues to work without cursor pagination,
    # matching the existing behaviour.
    # ---------------------------------------------------------

    if cursor and not search_value:
        created_at_iso, doc_id = _decode_cursor(cursor)

        query = query.start_after(
            {
                "created_at": datetime.fromisoformat(
                    created_at_iso
                ),
                "__name__": db.collection(
                    "products"
                ).document(doc_id),
            }
        )

    # ---------------------------------------------------------
    # Fetch products
    #
    # We fetch a larger working set when additional filters or
    # sorting are requested because those operations are handled
    # safely in Python rather than requiring new Firestore
    # composite indexes immediately.
    # ---------------------------------------------------------

    needs_python_filtering = any(
        [
            min_price is not None,
            max_price is not None,
            in_stock,
            verified_seller,
            sort_value in {"price_asc", "price_desc"},
        ]
    )

    fetch_limit = limit + 1

    if needs_python_filtering:
        # Keep this bounded so a large catalogue does not cause
        # an unreasonably large response.
        fetch_limit = min(
            max(limit * 5, 120),
            500,
        )

    query = query.limit(fetch_limit)

    docs = [d async for d in query.stream()]

    # ---------------------------------------------------------
    # Convert Firestore documents
    # ---------------------------------------------------------

    products = []

    for d in docs:
        p = d.to_dict()

        price = float(p.get("price", 0))
        stock = int(p.get("stock", 0))

        image_urls = p.get("image_urls") or []

        # Backward compatibility:
        # old products may only have image_url.
        if not image_urls and p.get("image_url"):
            image_urls = [p["image_url"]]

        products.append(
            {
                "id": d.id,
                "store_id": p["store_id"],
                "store_name": p.get("store_name"),
                "store_verified": p.get("store_verified"),
                "name": p["name"],
                "description": p.get("description"),
                "category": p.get("category"),
                "price": price,
                "stock": stock,
                "image_url": (
                    p.get("image_url")
                    or (image_urls[0] if image_urls else None)
                ),
                "image_urls": image_urls[:6],
                "status": p["status"],
                "created_at": p.get("created_at"),
            }
        )

    # ---------------------------------------------------------
    # Price filters
    # ---------------------------------------------------------

    if min_price is not None:
        products = [
            p
            for p in products
            if p["price"] >= min_price
        ]

    if max_price is not None:
        products = [
            p
            for p in products
            if p["price"] <= max_price
        ]

    # ---------------------------------------------------------
    # Stock filter
    # ---------------------------------------------------------

    if in_stock:
        products = [
            p
            for p in products
            if p["stock"] > 0
        ]

    # ---------------------------------------------------------
    # Verified seller filter
    # ---------------------------------------------------------

    if verified_seller:
        products = [
            p
            for p in products
            if p["store_verified"] is True
        ]

    # ---------------------------------------------------------
    # Sorting
    # ---------------------------------------------------------

    if sort_value == "price_asc":
        products.sort(
            key=lambda p: p["price"]
        )

    elif sort_value == "price_desc":
        products.sort(
            key=lambda p: p["price"],
            reverse=True,
        )

    elif sort_value == "newest":
        products.sort(
            key=lambda p: p["created_at"]
            or datetime.min,
            reverse=True,
        )

    # relevance keeps the existing Firestore ordering:
    # search -> name order
    # browse -> newest order

    # ---------------------------------------------------------
    # Pagination
    # ---------------------------------------------------------

    has_more = len(products) > limit

    products = products[:limit]

    # ---------------------------------------------------------
    # ProductOut response
    # ---------------------------------------------------------

    items = []

    for p in products:
        items.append(
            ProductOut(
                id=p["id"],
                store_id=p["store_id"],
                store_name=p["store_name"],
                store_verified=p["store_verified"],
                name=p["name"],
                description=p["description"],
                category=p["category"],
                price=p["price"],
                stock=p["stock"],
                image_url=p["image_url"],
                image_urls=p["image_urls"],
                status=p["status"],
            )
        )

    # ---------------------------------------------------------
    # Next cursor
    #
    # Keep cursor pagination for the normal browse view.
    # Filtered/sorted views can be expanded later with dedicated
    # Firestore indexes if the catalogue becomes large.
    # ---------------------------------------------------------

    next_cursor = None

    if (
        has_more
        and products
        and not search_value
        and sort_value in {"relevance", "newest"}
        and min_price is None
        and max_price is None
        and not in_stock
        and not verified_seller
    ):
        last = products[-1]

        created_at = last["created_at"]

        if created_at:
            if hasattr(created_at, "isoformat"):
                created_at_iso = created_at.isoformat()
            else:
                created_at_iso = str(created_at)

            next_cursor = _encode_cursor(
                created_at_iso,
                last["id"],
            )

    # ---------------------------------------------------------
    # Build response
    # ---------------------------------------------------------

    page = ProductPage(
        items=items,
        next_cursor=next_cursor,
    )

    # ---------------------------------------------------------
    # Cache
    # ---------------------------------------------------------

    await set_cached_products(
        redis,
        search_value,
        category_value,
        cursor,
        limit,
        page.model_dump(mode="json"),
        min_price=min_price,
        max_price=max_price,
        in_stock=in_stock,
        verified_seller=verified_seller,
        sort=sort_value,
    )

    return page
