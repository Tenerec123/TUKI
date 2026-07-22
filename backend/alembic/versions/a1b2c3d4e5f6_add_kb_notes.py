"""add kb_notes table

Revision ID: a1b2c3d4e5f6
Revises: 61d4fdf49e71
Create Date: 2026-07-22 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '61d4fdf49e71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create kb_notes table for knowledge base."""
    op.create_table(
        'kb_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('path', sa.Text(), nullable=False, unique=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(384), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_kb_notes_path', 'kb_notes', ['path'], unique=True)


def downgrade() -> None:
    """Drop kb_notes table."""
    op.drop_index('ix_kb_notes_path', table_name='kb_notes')
    op.drop_table('kb_notes')
