"""Refactor schema for review-scraping focus

Drops competitive-intelligence tables (snapshots, changes) and restructures
products / reviews / scrape_tasks around e-commerce review scraping. Adds the
crawl_batches table, expands the reviews table with analysis-friendly fields,
and introduces a unique constraint for deduplication.

Revision ID: a1b2c3d4e5f6
Revises: 08380b7909a9
Create Date: 2026-07-02 12:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# SQLite only auto-increments INTEGER PRIMARY KEY, not BIGINT PRIMARY KEY.
_BigIntPK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '08380b7909a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Drop competitive-intelligence tables ---
    op.drop_index('ix_changes_notification', table_name='changes')
    op.drop_index('ix_changes_product_date', table_name='changes')
    op.drop_table('changes')

    op.drop_index('ix_snapshots_product_captured', table_name='snapshots')
    op.drop_table('snapshots')

    # --- Products: drop competitive-only columns, add review-scraping columns ---
    op.add_column('products', sa.Column('platform', sa.String(length=32), nullable=False, server_default='tmall'))
    op.add_column('products', sa.Column('shop_id', sa.String(length=128), nullable=True))
    op.add_column('products', sa.Column('shop_name', sa.String(length=255), nullable=True))
    op.drop_column('products', 'source_type')
    op.drop_column('products', 'currency')
    op.drop_column('products', 'crawl_interval_minutes')
    op.drop_column('products', 'last_snapshot_id')
    op.create_index('ix_products_platform_ext', 'products', ['platform', 'external_product_id'])

    # --- Scrape tasks: add platform / retry / parent tracking + previously missing fields ---
    op.add_column('scrape_tasks', sa.Column('platform', sa.String(length=32), nullable=False, server_default='tmall'))
    op.add_column('scrape_tasks', sa.Column('task_type', sa.String(length=32), nullable=False, server_default='review_scrape'))
    op.add_column('scrape_tasks', sa.Column('name', sa.Text(), nullable=True))
    op.add_column('scrape_tasks', sa.Column('task_params', sa.JSON(), nullable=True))
    op.add_column('scrape_tasks', sa.Column('progress', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('scrape_tasks', sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('scrape_tasks', sa.Column('parent_task_id', sa.String(length=36), nullable=True))
    op.create_index('ix_scrape_tasks_product', 'scrape_tasks', ['product_id'])
    op.create_index('ix_scrape_tasks_type', 'scrape_tasks', ['task_type'])

    # --- Reviews: expand with analysis-friendly fields ---
    op.add_column('reviews', sa.Column('task_id', sa.String(length=36), nullable=True))
    op.add_column('reviews', sa.Column('platform', sa.String(length=32), nullable=False, server_default='tmall'))
    op.add_column('reviews', sa.Column('external_product_id', sa.String(length=128), nullable=True))
    op.add_column('reviews', sa.Column('user_id', sa.String(length=128), nullable=True))
    op.add_column('reviews', sa.Column('sku_id', sa.String(length=128), nullable=True))
    op.add_column('reviews', sa.Column('rating', sa.Numeric(precision=3, scale=1), nullable=True))
    op.add_column('reviews', sa.Column('media_urls', sa.JSON(), nullable=True))
    op.add_column('reviews', sa.Column('sentiment_score', sa.Numeric(precision=4, scale=3), nullable=True))
    op.add_column('reviews', sa.Column('sentiment_label', sa.String(length=16), nullable=True))
    op.add_column('reviews', sa.Column('dedup_hash', sa.String(length=64), nullable=True))
    op.add_column('reviews', sa.Column('raw_payload', sa.JSON(), nullable=True))

    op.create_index('ix_reviews_task', 'reviews', ['task_id'])
    op.create_index('ix_reviews_dedup', 'reviews', ['dedup_hash'])
    # SQLite cannot ALTER to add a unique constraint; use batch mode so the
    # migration works on both SQLite (copy-and-move) and Postgres/MySQL.
    with op.batch_alter_table('reviews') as batch_op:
        batch_op.create_unique_constraint('uq_review_platform_ext_review', ['platform', 'external_product_id', 'review_id'])

    # --- New: crawl_batches table (one row per captured API response) ---
    op.create_table(
        'crawl_batches',
        sa.Column('id', _BigIntPK, autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(length=36), nullable=False),
        sa.Column('product_id', sa.String(length=36), nullable=True),
        sa.Column('platform', sa.String(length=32), nullable=False, server_default='tmall'),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=True),
        sa.Column('total_pages', sa.Integer(), nullable=True),
        sa.Column('review_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fingerprint', sa.String(length=64), nullable=True),
        sa.Column('raw_payload', sa.JSON(), nullable=True),
        sa.Column('captured_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['scrape_tasks.task_id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_crawl_batches_task', 'crawl_batches', ['task_id'])
    op.create_index('ix_crawl_batches_task_page', 'crawl_batches', ['task_id', 'page_number'])


def downgrade() -> None:
    # Reverse crawl_batches
    op.drop_index('ix_crawl_batches_task_page', table_name='crawl_batches')
    op.drop_index('ix_crawl_batches_task', table_name='crawl_batches')
    op.drop_table('crawl_batches')

    # Reverse reviews expansion
    with op.batch_alter_table('reviews') as batch_op:
        batch_op.drop_constraint('uq_review_platform_ext_review', type_='unique')
    op.drop_index('ix_reviews_dedup', table_name='reviews')
    op.drop_index('ix_reviews_task', table_name='reviews')
    for col in ('raw_payload', 'dedup_hash', 'sentiment_label', 'sentiment_score',
                'media_urls', 'rating', 'sku_id', 'user_id', 'external_product_id', 'platform', 'task_id'):
        op.drop_column('reviews', col)

    # Reverse scrape_tasks
    op.drop_index('ix_scrape_tasks_type', table_name='scrape_tasks')
    op.drop_index('ix_scrape_tasks_product', table_name='scrape_tasks')
    op.drop_column('scrape_tasks', 'parent_task_id')
    op.drop_column('scrape_tasks', 'retry_count')
    op.drop_column('scrape_tasks', 'progress')
    op.drop_column('scrape_tasks', 'task_params')
    op.drop_column('scrape_tasks', 'name')
    op.drop_column('scrape_tasks', 'task_type')
    op.drop_column('scrape_tasks', 'platform')

    # Reverse products
    op.drop_index('ix_products_platform_ext', table_name='products')
    op.add_column('products', sa.Column('last_snapshot_id', sa.BigInteger(), nullable=True))
    op.add_column('products', sa.Column('crawl_interval_minutes', sa.Integer(), nullable=False, server_default='1440'))
    op.add_column('products', sa.Column('currency', sa.String(length=3), nullable=True))
    op.add_column('products', sa.Column('source_type', sa.String(length=32), nullable=False, server_default='tmall'))
    op.drop_column('products', 'shop_name')
    op.drop_column('products', 'shop_id')
    op.drop_column('products', 'platform')

    # Re-create competitive tables
    op.create_table(
        'snapshots',
        sa.Column('id', _BigIntPK, autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('product_id', sa.String(length=36), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('crawl_status', sa.String(length=32), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('list_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('rating', sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column('review_count', sa.Integer(), nullable=True),
        sa.Column('availability_status', sa.String(length=32), nullable=True),
        sa.Column('seller_name', sa.String(length=255), nullable=True),
        sa.Column('page_hash', sa.String(length=64), nullable=True),
        sa.Column('raw_payload', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_snapshots_product_captured', 'snapshots', ['product_id', 'captured_at'])

    op.create_table(
        'changes',
        sa.Column('id', _BigIntPK, autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('product_id', sa.String(length=36), nullable=False),
        sa.Column('baseline_snapshot_id', sa.BigInteger(), nullable=False),
        sa.Column('current_snapshot_id', sa.BigInteger(), nullable=False),
        sa.Column('change_date', sa.Date(), nullable=False),
        sa.Column('has_change', sa.Boolean(), nullable=False),
        sa.Column('change_types', sa.Text(), nullable=True),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('diff_payload', sa.Text(), nullable=True),
        sa.Column('ai_summary_title', sa.String(length=255), nullable=True),
        sa.Column('ai_summary_text', sa.Text(), nullable=True),
        sa.Column('ai_model', sa.String(length=64), nullable=True),
        sa.Column('notification_status', sa.String(length=32), nullable=False),
        sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['baseline_snapshot_id'], ['snapshots.id'], ),
        sa.ForeignKeyConstraint(['current_snapshot_id'], ['snapshots.id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_changes_product_date', 'changes', ['product_id', 'change_date'])
    op.create_index('ix_changes_notification', 'changes', ['tenant_id', 'notification_status', 'created_at'])
