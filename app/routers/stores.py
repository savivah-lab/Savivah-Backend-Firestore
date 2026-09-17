import re
from fastapi import APIRouter, Depends, HTTPException
from google.cloud.firestore import async_transactional
from app.core.firestore_client import get_db
from app.core.redis_client import get_redis
from app.deps import require_role
from app.services.firestore_helpers import new_id, now, claim_unique, UniquenessError
from app.services.cache import invalidate_product_cache
from app.schemas.product import (
    StoreCreateRequest,
    StoreOut,
    ProductCreateRequest,
    ProductUpdateRequest,
    ProductOut,
)

router = APIRouter(prefix="/api", tags=["stores"])


MAX_PRODUCT_IMAGES = 6


def _slugify(name: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))


def _product_images(product: dict) -> list[str]:
    """
    Return the complete product gallery.

    New products use image_urls.
    Older products may only have image_url, so fall back to that.
    """
    images = product.get("image_urls")

    if images:
        return [url for url in images if url][:MAX_PRODUCT_IMAGES]

    image_url = product.get("image_url")

    return [image_url] if image_url else []


def _product_out(product_id: str, product: dict) -> ProductOut:
    """
    Convert a Firestore product document into the public API schema.
    """
    images = _product_images(product)

    return ProductOut(
        id=product_id,
        store_id=product["store_id"],
        store_name=product.get("store_name"),
        store_verified=product.get("store_verified"),
        name=product["name"],
        description=product.get("description"),
        category=product.get("category"),
        price=float(product["price"]),
        stock=product["stock"],
        image_url=images[0] if images else None,
        image_urls=images,
        status=product["status"],
    )


async def _get_owned_store(db, store_id: str, user: dict) -> dict:
    """Every seller action re-checks ownership against a fresh Firestore
    read — never trusted from the JWT alone."""
    snap = await db.collection("stores").document(store_id).get()

    if not snap.exists or snap.to_dict().get("owner_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your store")

    store = snap.to_dict()
    store["id"] = snap.id
    return store


@router.post(
    "/stores",
    response_model=StoreOut,
)
async def create_store(
    body: StoreCreateRequest,
    user: dict = Depends(require_role("seller")),
    db=Depends(get_db),
):
    sid = new_id()
    slug = _slugify(body.name)

    store_doc = {
        "owner_id": user["id"],
        "name": body.name,
        "slug": slug,
        "business_reg_number": body.businessRegNumber,
        "verified": bool(body.businessRegNumber),
        "payout_method": body.payoutMethod,
        "payout_account": body.payoutAccount,
        "subscription_plan": "none",
        "subscription_expires_at": None,
        "status": "active",
        "created_at": now(),
    }

    @async_transactional
    async def create(transaction):
        await claim_unique(
            transaction,
            db,
            "store_slugs",
            slug,
            sid,
            "store name",
        )

        transaction.set(
            db.collection("stores").document(sid),
            store_doc,
        )

    try:
        await create(db.transaction())
    except UniquenessError:
        raise HTTPException(
            status_code=409,
            detail="You already have a store with that name",
        )

    return StoreOut(
        id=sid,
        name=store_doc["name"],
        slug=slug,
        verified=store_doc["verified"],
        payout_method=store_doc["payout_method"],
        payout_account=store_doc["payout_account"],
    )


@router.get(
    "/my/stores",
    response_model=list[StoreOut],
)
async def my_stores(
    user: dict = Depends(require_role("seller")),
    db=Depends(get_db),
):
    query = (
        db.collection("stores")
        .where("owner_id", "==", user["id"])
        .order_by("created_at", direction="DESCENDING")
    )

    out = []

    async for d in query.stream():
        s = d.to_dict()

        out.append(
            StoreOut(
                id=d.id,
                name=s["name"],
                slug=s["slug"],
                verified=s["verified"],
                payout_method=s.get("payout_method"),
                payout_account=s.get("payout_account"),
            )
        )

    return out


@router.post(
    "/stores/{store_id}/products",
    response_model=ProductOut,
)
async def create_product(
    store_id: str,
    body: ProductCreateRequest,
    user: dict = Depends(require_role("seller")),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    store = await _get_owned_store(db, store_id, user)
    pid = new_id()

    # Build gallery from imageUrls.
    images = [
        url.strip()
        for url in body.imageUrls
        if isinstance(url, str) and url.strip()
    ]

    # Keep backward compatibility with the existing imageUrl field.
    if body.imageUrl and body.imageUrl.strip():
        primary_image = body.imageUrl.strip()

        if primary_image in images:
            images.remove(primary_image)

        images.insert(0, primary_image)

    # Maximum six images per product.
    images = images[:MAX_PRODUCT_IMAGES]

    product_doc = {
        "store_id": store_id,
        "store_name": store["name"],
        "store_verified": store["verified"],
        "name": body.name,
        "name_lower": body.name.lower(),
        "description": body.description,
        "category": body.category,
        "price": body.price,
        "stock": body.stock,

        # Backward-compatible primary image.
        "image_url": images[0] if images else None,

        # New complete gallery.
        "image_urls": images,

        "is_featured": False,
        "featured_until": None,
        "status": "active",
        "created_at": now(),
    }

    await db.collection("products").document(pid).set(product_doc)

    await invalidate_product_cache(redis)

    return _product_out(pid, product_doc)


@router.get(
    "/stores/{store_id}/products",
    response_model=list[ProductOut],
)
async def seller_products(
    store_id: str,
    user: dict = Depends(require_role("seller")),
    db=Depends(get_db),
):
    """Unlike the public /api/products list, this shows EVERY status for
    the owning seller only — same as the Postgres version."""
    await _get_owned_store(db, store_id, user)

    query = (
        db.collection("products")
        .where("store_id", "==", store_id)
        .order_by("created_at", direction="DESCENDING")
    )

    out = []

    async for d in query.stream():
        p = d.to_dict()
        out.append(_product_out(d.id, p))

    return out


@router.put(
    "/products/{product_id}",
    response_model=ProductOut,
)
async def update_product(
    product_id: str,
    body: ProductUpdateRequest,
    user: dict = Depends(require_role("seller")),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    ref = db.collection("products").document(product_id)
    snap = await ref.get()

    if not snap.exists:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    p = snap.to_dict()

    await _get_owned_store(db, p["store_id"], user)

    updates = {}

    # Normal fields.
    if body.name is not None:
        updates["name"] = body.name
        updates["name_lower"] = body.name.lower()

    if body.description is not None:
        updates["description"] = body.description

    if body.category is not None:
        updates["category"] = body.category

    if body.price is not None:
        updates["price"] = body.price

    if body.stock is not None:
        updates["stock"] = body.stock

    if body.status is not None:
        updates["status"] = body.status

    # ---------------------------------------------------------
    # Image handling
    # ---------------------------------------------------------

    if body.imageUrls is not None:
        # imageUrls explicitly supplied.
        # [] means the seller intentionally removed all images.
        images = [
            url.strip()
            for url in body.imageUrls
            if isinstance(url, str) and url.strip()
        ]

        if body.imageUrl and body.imageUrl.strip():
            primary_image = body.imageUrl.strip()

            if primary_image in images:
                images.remove(primary_image)

            images.insert(0, primary_image)

        images = images[:MAX_PRODUCT_IMAGES]

        updates["image_urls"] = images
        updates["image_url"] = images[0] if images else None

    elif body.imageUrl is not None:
        # Only imageUrl was supplied.
        # Treat it as the new primary image while preserving
        # the other existing gallery images.
        primary_image = body.imageUrl.strip() if body.imageUrl else ""

        existing_images = _product_images(p)

        if primary_image:
            if primary_image in existing_images:
                existing_images.remove(primary_image)

            existing_images.insert(0, primary_image)
            existing_images = existing_images[:MAX_PRODUCT_IMAGES]
        else:
            existing_images = []

        updates["image_urls"] = existing_images
        updates["image_url"] = existing_images[0] if existing_images else None

    # ---------------------------------------------------------
    # Save
    # ---------------------------------------------------------

    if updates:
        await ref.update(updates)

    p.update(updates)

    await invalidate_product_cache(redis)

    return _product_out(product_id, p)
