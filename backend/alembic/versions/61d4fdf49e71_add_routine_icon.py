"""add routine icon

Revision ID: 61d4fdf49e71
Revises: da54dc301614
Create Date: 2026-06-17 20:00:24.996112

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '61d4fdf49e71'
down_revision: Union[str, Sequence[str], None] = 'da54dc301614'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('routines', sa.Column('icon', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('routines', 'icon')
