import re
from fastapi import APIRouter, Depends, HTTPException
from google.cloud.firestore import async_transactional
from app.core.firestore_client import get_db
from app.core.redis_client import get_redis
from app.deps import require_role
from app.services.firestore_helpers import new_id, now, claim_unique, UniquenessError
from app.services.cache import invalidate_product_cache
from app.schemas.product import StoreCreateRequest, StoreOut, ProductCreateRequest, ProductUpdateRequest, ProductOut

router = APIRouter(prefix="/api", tags=["stores"])


def _slugify(name: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))


async def _get_owned_store(db, store_id: str, user: dict) -> dict:
    """Every seller action re-checks ownership against a fresh Firestore
    read — never trusted from the JWT alone."""
    snap = await db.collection("stores").document(store_id).get()
    if not snap.exists or snap.to_dict().get("owner_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your store")
    store = snap.to_dict()
    store["id"] = snap.id
    return store


@router.post("/stores", response_model=StoreOut)
async def create_store(body: StoreCreateRequest, user: dict = Depends(require_role("seller")), db=Depends(get_db)):
    sid = new_id()
    slug = _slugify(body.name)
    store_doc = {
        "owner_id": user["id"], "name": body.name, "slug": slug,
        "business_reg_number": body.businessRegNumber, "verified": bool(body.businessRegNumber),
        "payout_method": body.payoutMethod, "payout_account": body.payoutAccount,
        "subscription_plan": "none", "subscription_expires_at": None, "status": "active", "created_at": now(),
    }

    @async_transactional
    async def create(transaction):
        await claim_unique(transaction, db, "store_slugs", slug, sid, "store name")
        transaction.set(db.collection("stores").document(sid), store_doc)

    try:
        await create(db.transaction())
    except UniquenessError:
        raise HTTPException(status_code=409, detail="You already have a store with that name")

    return StoreOut(id=sid, name=store_doc["name"], slug=slug, verified=store_doc["verified"],
                      payout_method=store_doc["payout_method"], payout_account=store_doc["payout_account"])


@router.get("/my/stores", response_model=list[StoreOut])
async def my_stores(user: dict = Depends(require_role("seller")), db=Depends(get_db)):
    query = db.collection("stores").where("owner_id", "==", user["id"]).order_by("created_at", direction="DESCENDING")
    out = []
    async for d in query.stream():
        s = d.to_dict()
        out.append(StoreOut(id=d.id, name=s["name"], slug=s["slug"], verified=s["verified"],
                              payout_method=s.get("payout_method"), payout_account=s.get("payout_account")))
    return out


@router.post("/stores/{store_id}/products", response_model=ProductOut)
async def create_product(store_id: str, body: ProductCreateRequest, user: dict = Depends(require_role("seller")),
                           db=Depends(get_db), redis=Depends(get_redis)):
    store = await _get_owned_store(db, store_id, user)
    pid = new_id()
    product_doc = {
        "store_id": store_id, "store_name": store["name"], "store_verified": store["verified"],
        "name": body.name, "name_lower": body.name.lower(), "description": body.description,
        "category": body.category, "price": body.price, "stock": body.stock, "image_url": body.imageUrl,
        "is_featured": False, "featured_until": None, "status": "active", "created_at": now(),
    }
    await db.collection("products").document(pid).set(product_doc)
    await invalidate_product_cache(redis)
    return ProductOut(id=pid, store_id=store_id, name=product_doc["name"], description=product_doc["description"],
                        category=product_doc["category"], price=float(product_doc["price"]), stock=product_doc["stock"],
                        image_url=product_doc["image_url"], status=product_doc["status"])


@router.get("/stores/{store_id}/products", response_model=list[ProductOut])
async def seller_products(store_id: str, user: dict = Depends(require_role("seller")), db=Depends(get_db)):
    """Unlike the public /api/products list, this shows EVERY status for
    the owning seller only — same as the Postgres version."""
    await _get_owned_store(db, store_id, user)
    query = db.collection("products").where("store_id", "==", store_id).order_by("created_at", direction="DESCENDING")
    out = []
    async for d in query.stream():
        p = d.to_dict()
        out.append(ProductOut(id=d.id, store_id=p["store_id"], name=p["name"], description=p.get("description"),
                                category=p.get("category"), price=float(p["price"]), stock=p["stock"],
                                image_url=p.get("image_url"), status=p["status"]))
    return out


@router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(product_id: str, body: ProductUpdateRequest, user: dict = Depends(require_role("seller")),
                           db=Depends(get_db), redis=Depends(get_redis)):
    ref = db.collection("products").document(product_id)
    snap = await ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Product not found")
    p = snap.to_dict()
    await _get_owned_store(db, p["store_id"], user)

    updates = {}
    for field, value in body.model_dump(exclude_none=True).items():
        key = "image_url" if field == "imageUrl" else field
        updates[key] = value
    if "name" in updates:
        updates["name_lower"] = updates["name"].lower()

    if updates:
        await ref.update(updates)
    p.update(updates)
    await invalidate_product_cache(redis)
    return ProductOut(id=product_id, store_id=p["store_id"], name=p["name"], description=p.get("description"),
                        category=p.get("category"), price=float(p["price"]), stock=p["stock"],
                        image_url=p.get("image_url"), status=p["status"])
