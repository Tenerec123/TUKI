"""add conversation total cost

Revision ID: d1c2b3a405f6
Revises: c7d8e9f0a1b2
Create Date: 2026-09-19 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1c2b3a405f6'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add total_cost to conversations with a zero default (legacy backfill)."""
    op.add_column(
        'conversations',
        sa.Column('total_cost', sa.Numeric(12, 6), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Drop total_cost from conversations."""
    op.drop_column('conversations', 'total_cost')