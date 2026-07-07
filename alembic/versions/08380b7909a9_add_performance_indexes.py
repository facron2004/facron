"""Add performance indexes

Revision ID: 08380b7909a9
Revises: c772a2bb3817
Create Date: 2026-06-13 09:40:40.100861

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '08380b7909a9'
down_revision: Union[str, None] = 'c772a2bb3817'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add composite index for common queries
    op.create_index('ix_scrape_tasks_status_created', 'scrape_tasks', ['status', 'created_at'], unique=False)
    op.create_index('ix_scrape_tasks_product_status', 'scrape_tasks', ['product_id', 'status'], unique=False)

    # Add index for products queries
    op.create_index('ix_products_normalized_url', 'products', ['normalized_url'], unique=False)

    # Add index for reviews queries
    op.create_index('ix_reviews_product_date', 'reviews', ['product_id', 'review_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_reviews_product_date', table_name='reviews')
    op.drop_index('ix_products_normalized_url', table_name='products')
    op.drop_index('ix_scrape_tasks_product_status', table_name='scrape_tasks')
    op.drop_index('ix_scrape_tasks_status_created', table_name='scrape_tasks')
