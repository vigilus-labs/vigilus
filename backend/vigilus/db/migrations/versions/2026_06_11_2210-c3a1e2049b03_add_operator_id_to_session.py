"""Add operator_id to Session

Revision ID: c3a1e2049b03
Revises: b7c2f1038a02
Create Date: 2026-06-11 22:10:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a1e2049b03'
down_revision: Union[str, None] = 'b7c2f1038a02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sessions', sa.Column('operator_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column('sessions', 'operator_id')
