"""operator max_iterations and action tool output

Revision ID: c4e8a1b2d390
Revises: b8c3d4e5f6a7
Create Date: 2026-09-25 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1b2d390"
down_revision: str | None = "b8c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("operators", "max_iterations"):
        op.add_column("operators", sa.Column("max_iterations", sa.Integer(), nullable=True))
    if not _has_column("actions", "output"):
        op.add_column("actions", sa.Column("output", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("actions", "output"):
        op.drop_column("actions", "output")
    if _has_column("operators", "max_iterations"):
        op.drop_column("operators", "max_iterations")
