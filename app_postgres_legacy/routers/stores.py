import re
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.core.redis_client import get_redis
from app.deps import require_role
from app.models.user import User
from app.models.store import Store
from app.models.product import Product
from app.schemas.product import StoreCreateRequest, StoreOut, ProductCreateRequest, ProductUpdateRequest, ProductOut
from app.services.cache import invalidate_product_cache

router = APIRouter(prefix="/api", tags=["stores"])


def _slugify(name: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))


async def _get_owned_store(db: AsyncSession, store_id: uuid.UUID, user: User) -> Store:
    """Every seller action re-checks ownership against a fresh DB read —
    never trusted from the JWT alone."""
    store = await db.get(Store, store_id)
    if not store or store.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not your store")
    return store


@router.post("/stores", response_model=StoreOut)
async def create_store(
    body: StoreCreateRequest,
    user: User = Depends(require_role("seller")),
    db: AsyncSession = Depends(get_db),
):
    store = Store(
        owner_id=user.id, name=body.name, slug=_slugify(body.name),
        business_reg_number=body.businessRegNumber, payout_method=body.payoutMethod,
        payout_account=body.payoutAccount, verified=bool(body.businessRegNumber),
    )
    db.add(store)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="You already have a store with that name")
    await db.refresh(store)
    return store


@router.get("/my/stores", response_model=list[StoreOut])
async def my_stores(user: User = Depends(require_role("seller")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Store).where(Store.owner_id == user.id).order_by(Store.created_at.desc())
    )).scalars().all()
    return rows


@router.post("/stores/{store_id}/products", response_model=ProductOut)
async def create_product(
    store_id: uuid.UUID, body: ProductCreateRequest,
    user: User = Depends(require_role("seller")), db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    await _get_owned_store(db, store_id, user)
    product = Product(
        store_id=store_id, name=body.name, description=body.description,
        category=body.category, price=body.price, stock=body.stock, image_url=body.imageUrl,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    await invalidate_product_cache(redis)
    return ProductOut(
        id=product.id, store_id=product.store_id, name=product.name, description=product.description,
        category=product.category, price=float(product.price), stock=product.stock,
        image_url=product.image_url, status=product.status,
    )


@router.get("/stores/{store_id}/products", response_model=list[ProductOut])
async def seller_products(
    store_id: uuid.UUID, user: User = Depends(require_role("seller")), db: AsyncSession = Depends(get_db),
):
    """Unlike the public /api/products list, this shows EVERY status
    (hidden, out_of_stock included) for the owning seller only."""
    await _get_owned_store(db, store_id, user)
    rows = (await db.execute(
        select(Product).where(Product.store_id == store_id).order_by(Product.created_at.desc())
    )).scalars().all()
    return [
        ProductOut(id=p.id, store_id=p.store_id, name=p.name, description=p.description,
                    category=p.category, price=float(p.price), stock=p.stock,
                    image_url=p.image_url, status=p.status)
        for p in rows
    ]


@router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID, body: ProductUpdateRequest,
    user: User = Depends(require_role("seller")), db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await _get_owned_store(db, product.store_id, user)

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(product, "image_url" if field == "imageUrl" else field, value)

    await db.commit()
    await db.refresh(product)
    await invalidate_product_cache(redis)
    return ProductOut(
        id=product.id, store_id=product.store_id, name=product.name, description=product.description,
        category=product.category, price=float(product.price), stock=product.stock,
        image_url=product.image_url, status=product.status,
    )
