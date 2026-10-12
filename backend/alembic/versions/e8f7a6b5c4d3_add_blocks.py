"""add blocks and block exceptions tables

Revision ID: e8f7a6b5c4d3
Revises: f5e4d3c2b1a0
Create Date: 2026-10-11 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'e8f7a6b5c4d3'
down_revision: Union[str, Sequence[str], None] = 'f5e4d3c2b1a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create blocks (recurring events) and block_exceptions tables."""
    op.create_table(
        'blocks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(512), nullable=False),
        sa.Column('description', sa.String(512), nullable=True),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=False),
        sa.Column('rrule', sa.String(512), nullable=False),
        sa.Column('embedding', Vector(384), nullable=True),
    )
    op.create_table(
        'block_exceptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('block_id', sa.Integer(), nullable=False),
        sa.Column('is_add', sa.Boolean(), nullable=False),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['block_id'], ['blocks.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('block_id', 'start_time', name='uq_block_exception_block_start'),
        sa.CheckConstraint(
            '(NOT is_add AND end_time IS NULL) OR (is_add AND end_time IS NOT NULL)',
            name='ck_block_exception_kind_times',
        ),
    )
    op.create_index('ix_block_exceptions_block_id', 'block_exceptions', ['block_id'])


def downgrade() -> None:
    """Drop block_exceptions and blocks tables."""
    op.drop_index('ix_block_exceptions_block_id', table_name='block_exceptions')
    op.drop_table('block_exceptions')
    op.drop_table('blocks')
