"""Add scrape_task_reviews association table.

Revision ID: 9b7d4a1c2e3f
Revises: a1b2c3d4e5f6
Create Date: 2026-07-06 17:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9b7d4a1c2e3f'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BigIntPK = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table(
        'scrape_task_reviews',
        sa.Column('id', _BigIntPK, autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(length=36), nullable=False),
        sa.Column('review_id', _BigIntPK, nullable=False),
        sa.Column('product_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['scrape_tasks.task_id']),
        sa.ForeignKeyConstraint(['review_id'], ['reviews.id']),
        sa.ForeignKeyConstraint(['product_id'], ['products.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'review_id', name='uq_scrape_task_reviews_task_review'),
    )
    op.create_index('ix_scrape_task_reviews_task', 'scrape_task_reviews', ['task_id'])
    op.create_index('ix_scrape_task_reviews_product', 'scrape_task_reviews', ['product_id'])


def downgrade() -> None:
    op.drop_index('ix_scrape_task_reviews_product', table_name='scrape_task_reviews')
    op.drop_index('ix_scrape_task_reviews_task', table_name='scrape_task_reviews')
    op.drop_table('scrape_task_reviews')
