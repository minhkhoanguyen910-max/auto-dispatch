from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import Product
from app.schemas import ProductCreate, ProductOut, ProductUpdate

router = APIRouter(prefix="/products", tags=["products"], dependencies=[Depends(get_current_user)])


async def _ensure_unique(session: AsyncSession, name: str | None, sku: str | None, exclude_id: int | None = None) -> None:
    others = select(Product.id).where(Product.id != exclude_id) if exclude_id is not None else select(Product.id)
    if name is not None and await session.scalar(others.where(func.lower(Product.name) == name.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "A product with this name already exists")
    if sku is not None and await session.scalar(others.where(Product.sku == sku)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Another product already uses this SKU")


@router.get("", response_model=list[ProductOut])
async def list_products(session: AsyncSession = Depends(get_session)) -> list[Product]:
    return list(await session.scalars(select(Product).order_by(Product.name)))


@router.post("", response_model=ProductOut, status_code=201)
async def create_product(body: ProductCreate, session: AsyncSession = Depends(get_session)) -> Product:
    name, sku = body.name.strip(), (body.sku or "").strip() or None
    await _ensure_unique(session, name, sku)
    product = Product(name=name, sku=sku, price=body.price)
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(product_id: int, body: ProductUpdate, session: AsyncSession = Depends(get_session)) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    name = body.name.strip() if body.name is not None else None
    # An empty SKU clears it
    sku = ((body.sku or "").strip() or None) if "sku" in body.model_fields_set else product.sku
    await _ensure_unique(session, name, sku, exclude_id=product.id)

    if name is not None:
        product.name = name
    product.sku = sku
    if body.price is not None:
        product.price = body.price
    if body.is_active is not None:
        product.is_active = body.is_active
    await session.commit()
    await session.refresh(product)
    return product
