"""Add expected_pages to scrape_tasks

Tracks the most recent total_pages observed from a Tmall review payload so
the UI can show "captured X / expected Y" and the worker can align to
totalPages when max_pages=0.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-07-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('scrape_tasks', sa.Column('expected_pages', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('scrape_tasks', 'expected_pages')
