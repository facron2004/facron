"""ORM models for the review scraping platform.

This module is focused exclusively on e-commerce review scraping and analysis.
Competitive-intelligence tables (Snapshot, Change) have been removed.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    JSON,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from review_scraper.core.database import Base

# SQLite only auto-increments `INTEGER PRIMARY KEY`, not `BIGINT PRIMARY KEY`.
# Use INTEGER on SQLite, BIGINT on PostgreSQL/MySQL.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class Product(Base):
    """A product being tracked for reviews across one or more scrape tasks."""

    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), default="default")
    platform: Mapped[str] = mapped_column(String(32), default="tmall")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    external_product_id: Mapped[str | None] = mapped_column(String(128))
    marketplace: Mapped[str | None] = mapped_column(String(64))
    shop_id: Mapped[str | None] = mapped_column(String(128))
    shop_name: Mapped[str | None] = mapped_column(String(255))
    title_current: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_products_tenant_status", "tenant_id", "status"),
        Index("ix_products_platform_ext", "platform", "external_product_id"),
    )


class ScrapeTask(Base):
    """A single review-scraping job."""

    __tablename__ = "scrape_tasks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id"))
    platform: Mapped[str] = mapped_column(String(32), default="tmall")
    task_type: Mapped[str] = mapped_column(String(32), default="review_scrape")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    name: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    task_params: Mapped[dict | None] = mapped_column(JSON)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int | None] = mapped_column(Integer, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer, default=0)
    expected_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    file_paths: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    parent_task_id: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_scrape_tasks_status", "status"),
        Index("ix_scrape_tasks_type", "task_type"),
        Index("ix_scrape_tasks_product", "product_id"),
    )


class CrawlBatch(Base):
    """One captured API response / page during a scrape task.

    Persisting every batch gives us raw auditability, retry-by-page, and
    post-hoc re-parsing without re-scraping the site.
    """

    __tablename__ = "crawl_batches"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("scrape_tasks.task_id"), nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id"))
    platform: Mapped[str] = mapped_column(String(32), default="tmall")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_crawl_batches_task", "task_id"),
        Index("ix_crawl_batches_task_page", "task_id", "page_number"),
    )


class Review(Base):
    """A single review row, normalized across platforms."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scrape_tasks.task_id"))
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="tmall")
    external_product_id: Mapped[str | None] = mapped_column(String(128))
    review_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_nick: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[str | None] = mapped_column(String(128))
    review_date: Mapped[str | None] = mapped_column(String(32))
    sku: Mapped[str | None] = mapped_column(String(255))
    sku_id: Mapped[str | None] = mapped_column(String(128))
    rating: Mapped[float | None] = mapped_column(Numeric(3, 1))
    content: Mapped[str | None] = mapped_column(Text)
    append_content: Mapped[str | None] = mapped_column(Text)
    append_date: Mapped[str | None] = mapped_column(String(32))
    helpful_count: Mapped[int | None] = mapped_column(Integer, default=0)
    page_number: Mapped[int | None] = mapped_column(Integer)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    picture_urls: Mapped[str | None] = mapped_column(Text)
    media_urls: Mapped[dict | None] = mapped_column(JSON)
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    sentiment_label: Mapped[str | None] = mapped_column(String(16))
    dedup_hash: Mapped[str | None] = mapped_column(String(64))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    source_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("platform", "external_product_id", "review_id", name="uq_review_platform_ext_review"),
        Index("ix_reviews_product", "product_id"),
        Index("ix_reviews_task", "task_id"),
        Index("ix_reviews_review_id", "review_id"),
        Index("ix_reviews_dedup", "dedup_hash"),
    )


class ReviewAnalysisResult(Base):
    """Cached full-analysis output for a scrape task."""

    __tablename__ = "review_analysis_results"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("scrape_tasks.task_id"), nullable=False)
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    keywords_json: Mapped[dict | None] = mapped_column(JSON)
    sentiment_json: Mapped[dict | None] = mapped_column(JSON)
    insights_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_review_analysis_task", "task_id"),
    )


class ScrapeTaskReview(Base):
    """Association table linking a task to every review it captured."""

    __tablename__ = "scrape_task_reviews"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("scrape_tasks.task_id"), nullable=False)
    review_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("reviews.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("task_id", "review_id", name="uq_scrape_task_reviews_task_review"),
        Index("ix_scrape_task_reviews_task", "task_id"),
        Index("ix_scrape_task_reviews_product", "product_id"),
    )


class TaskLog(Base):
    """Structured logs attached to a scrape task."""

    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("scrape_tasks.task_id"), nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info")
    stage: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    extra_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_task_logs_task_created", "task_id", "created_at"),
    )


class TaskArtifact(Base):
    """Files produced by a scrape task (csv/xlsx/json exports, screenshots, html)."""

    __tablename__ = "task_artifacts"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("scrape_tasks.task_id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_task_artifacts_task", "task_id"),
    )
