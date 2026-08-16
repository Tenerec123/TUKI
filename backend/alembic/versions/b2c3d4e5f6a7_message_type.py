"""replace messages.is_user bool with messages.type string

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-15 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add messages.type and backfill from is_user, then drop is_user."""
    op.add_column('messages', sa.Column('type', sa.String(16), nullable=True))

    conn = op.get_bind()
    conn.execute(sa.text("UPDATE messages SET type = 'prompt' WHERE is_user = true"))
    conn.execute(sa.text("UPDATE messages SET type = 'agent' WHERE is_user = false OR is_user IS NULL"))

    op.alter_column('messages', 'type', nullable=False)
    op.drop_column('messages', 'is_user')


def downgrade() -> None:
    """Add messages.is_user back and derive from type, then drop type."""
    op.add_column('messages', sa.Column('is_user', sa.Boolean(), nullable=True))

    conn = op.get_bind()
    conn.execute(sa.text("UPDATE messages SET is_user = (type = 'prompt') WHERE is_user IS NULL"))

    op.alter_column('messages', 'is_user', nullable=False)
    op.drop_column('messages', 'type')
