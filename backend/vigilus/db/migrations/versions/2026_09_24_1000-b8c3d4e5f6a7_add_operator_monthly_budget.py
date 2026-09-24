"""add operator monthly_budget_usd

Revision ID: b8c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-24 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("operators")}
    if "monthly_budget_usd" not in columns:
        op.add_column(
            "operators",
            sa.Column("monthly_budget_usd", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("operators")}
    if "monthly_budget_usd" in columns:
        op.drop_column("operators", "monthly_budget_usd")
