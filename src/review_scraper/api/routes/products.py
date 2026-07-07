"""Product management API endpoints.

Re-scoped around e-commerce review scraping: register a product (by URL) so
that multiple scrape tasks can be attached to it. The old competitive-
intelligence "watch" semantics have been removed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from review_scraper.api.routes.reviews import get_db, _validate_url
from review_scraper.models import Product, ScrapeTask

router = APIRouter(tags=["products"])


class ProductCreateRequest(BaseModel):
    source_url: HttpUrl
    platform: str = "tmall"
    external_product_id: str | None = None


class ProductCreateResponse(BaseModel):
    product_id: str
    platform: str
    external_product_id: str | None
    message: str


class ProductListItem(BaseModel):
    id: str
    platform: str
    external_product_id: str | None
    source_url: str
    title: str | None
    status: str
    last_crawled_at: str | None
    task_count: int = 0
    latest_task_id: str | None = None


@router.post("/products", response_model=ProductCreateResponse, summary="注册商品")
async def create_product(payload: ProductCreateRequest, db: Session = Depends(get_db)) -> ProductCreateResponse:
    """注册一个商品用于后续评论抓取。URL 必须命中平台白名单。"""
    _validate_url(str(payload.source_url))
    product = (
        db.query(Product)
        .filter(
            Product.platform == payload.platform,
            Product.external_product_id == payload.external_product_id,
        )
        .first()
    )
    if product is None:
        product = Product(
            platform=payload.platform,
            source_url=str(payload.source_url),
            normalized_url=str(payload.source_url),
            external_product_id=payload.external_product_id,
        )
        db.add(product)
        db.commit()
        db.refresh(product)
    return ProductCreateResponse(
        product_id=product.id,
        platform=product.platform,
        external_product_id=product.external_product_id,
        message=f"Product registered for {payload.platform}",
    )


@router.get("/products", summary="商品列表")
async def list_products(
    platform: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[ProductListItem]:
    query = db.query(Product)
    if platform:
        query = query.filter(Product.platform == platform)
    products = query.order_by(Product.created_at.desc()).limit(limit).all()
    items: list[ProductListItem] = []
    for p in products:
        task_query = db.query(ScrapeTask).filter(ScrapeTask.product_id == p.id).order_by(ScrapeTask.created_at.desc())
        latest_task = task_query.first()
        items.append(ProductListItem(
            id=p.id,
            platform=p.platform,
            external_product_id=p.external_product_id,
            source_url=p.source_url,
            title=p.title_current,
            status=p.status,
            last_crawled_at=p.last_crawled_at.isoformat() if p.last_crawled_at else None,
            task_count=task_query.count(),
            latest_task_id=latest_task.task_id if latest_task else None,
        ))
    return items
