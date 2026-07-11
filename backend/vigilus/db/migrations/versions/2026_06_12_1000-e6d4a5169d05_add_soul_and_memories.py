"""Add Operator.soul and the memories table

Revision ID: e6d4a5169d05
Revises: d4b2f3058c04
Create Date: 2026-06-12 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6d4a5169d05"
down_revision: str | None = "d4b2f3058c04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operators", sa.Column("soul", sa.Text(), nullable=True))
    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False, server_default="global"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memories_scope", "memories", ["scope"])


def downgrade() -> None:
    op.drop_index("ix_memories_scope", table_name="memories")
    op.drop_table("memories")
    op.drop_column("operators", "soul")
