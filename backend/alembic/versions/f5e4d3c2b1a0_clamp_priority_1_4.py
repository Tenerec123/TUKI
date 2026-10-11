"""clamp priority values to the 1-4 vocabulary

Revision ID: f5e4d3c2b1a0
Revises: d1c2b3a405f6
Create Date: 2026-10-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5e4d3c2b1a0'
down_revision: Union[str, Sequence[str], None] = 'd1c2b3a405f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rescale legacy 1-64 priorities onto the 1-4 vocabulary (no schema change).

    Legacy data used a 1..64 scale where a HIGHER number meant a MORE
    important item (the creation form defaulted to 1, the least important).
    The new vocabulary is 1=critical, 2=high, 3=normal, 4=low, so the mapping
    is inverted and compressed linearly: legacy 1 -> 4 (low), legacy 64 -> 1
    (critical), and the middle (32) lands on 3 (normal). NULL or < 1 (unset /
    default) becomes 3 (normal).
    """
    for table in ('tasks', 'routines', 'projects'):
        op.execute(
            f"UPDATE {table} SET priority = "
            f"LEAST(4, GREATEST(1, ROUND(4 - 3.0 * (priority - 1) / 63)::int)) "
            f"WHERE priority >= 1"
        )
        op.execute(f"UPDATE {table} SET priority = 3 WHERE priority IS NULL OR priority < 1")


def downgrade() -> None:
    """Data-only migration: clamped values cannot be restored."""
    pass