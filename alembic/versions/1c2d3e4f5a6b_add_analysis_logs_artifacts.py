"""Add analysis/log/artifact tables.

Revision ID: 1c2d3e4f5a6b
Revises: 9b7d4a1c2e3f
Create Date: 2026-07-06 17:08:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1c2d3e4f5a6b'
down_revision: Union[str, None] = '9b7d4a1c2e3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BigIntPK = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table(
        'review_analysis_results',
        sa.Column('id', _BigIntPK, autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(length=36), nullable=False),
        sa.Column('summary_json', sa.JSON(), nullable=True),
        sa.Column('keywords_json', sa.JSON(), nullable=True),
        sa.Column('sentiment_json', sa.JSON(), nullable=True),
        sa.Column('insights_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['scrape_tasks.task_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_review_analysis_task', 'review_analysis_results', ['task_id'])

    op.create_table(
        'task_logs',
        sa.Column('id', _BigIntPK, autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(length=36), nullable=False),
        sa.Column('level', sa.String(length=16), nullable=False, server_default='info'),
        sa.Column('stage', sa.String(length=64), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('extra_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['scrape_tasks.task_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_task_logs_task_created', 'task_logs', ['task_id', 'created_at'])

    op.create_table(
        'task_artifacts',
        sa.Column('id', _BigIntPK, autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(length=36), nullable=False),
        sa.Column('artifact_type', sa.String(length=32), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=True),
        sa.Column('mime_type', sa.String(length=128), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['scrape_tasks.task_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_task_artifacts_task', 'task_artifacts', ['task_id'])


def downgrade() -> None:
    op.drop_index('ix_task_artifacts_task', table_name='task_artifacts')
    op.drop_table('task_artifacts')
    op.drop_index('ix_task_logs_task_created', table_name='task_logs')
    op.drop_table('task_logs')
    op.drop_index('ix_review_analysis_task', table_name='review_analysis_results')
    op.drop_table('review_analysis_results')
