"""Database Structure

Revision ID: fa777b7c71df
Revises: 4a0ae807c41d
Create Date: 2026-05-16 12:04:04.605904

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fa777b7c71df'
down_revision: Union[str, Sequence[str], None] = '4a0ae807c41d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Añadimos la columna permitiendo nulos temporalmente y eliminamos las viejas
    with op.batch_alter_table('routines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('init_date', sa.Date(), nullable=True))
        batch_op.drop_column('next_run')
        batch_op.drop_column('last_run')

    # 2. Inyectamos la fecha por defecto a los registros existentes para que no tengan NULL
    op.execute("UPDATE routines SET init_date = '2026-05-16' WHERE init_date IS NULL")

    # 3. Aplicamos el estado definitivo de tu modelo (nullable=True)
    with op.batch_alter_table('routines', schema=None) as batch_op:
        batch_op.alter_column('init_date', nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('routines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_run', sa.DATE(), nullable=True))
        batch_op.add_column(sa.Column('next_run', sa.DATE(), nullable=True))
        batch_op.drop_column('init_date')