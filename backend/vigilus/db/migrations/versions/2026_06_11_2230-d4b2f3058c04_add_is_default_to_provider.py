"""Add is_default to Provider

Revision ID: d4b2f3058c04
Revises: c3a1e2049b03
Create Date: 2026-06-11 22:30:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4b2f3058c04"
down_revision: str | None = "c3a1e2049b03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("is_default", sa.Boolean(), nullable=True))
    op.execute("UPDATE providers SET is_default = 0")


def downgrade() -> None:
    op.drop_column("providers", "is_default")
