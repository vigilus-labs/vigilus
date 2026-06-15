"""Add is_default to Provider

Revision ID: d4b2f3058c04
Revises: c3a1e2049b03
Create Date: 2026-06-11 22:30:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4b2f3058c04'
down_revision: Union[str, None] = 'c3a1e2049b03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('providers', sa.Column('is_default', sa.Boolean(), nullable=True))
    op.execute("UPDATE providers SET is_default = 0")

def downgrade() -> None:
    op.drop_column('providers', 'is_default')
