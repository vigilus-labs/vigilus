"""cache token counts on llm_usage

Revision ID: e7b2c3d4f5a6
Revises: d5f9b2c3e4a1
Create Date: 2026-09-26 13:00:00.000000+00:00

``compression`` is a new UsageActorType value. SQLite stores the enum as a
string, so no type change is required there. Postgres needs the value added
to the native enum.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b2c3d4f5a6"
down_revision: str | None = "d5f9b2c3e4a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return name in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("llm_usage", "cache_read_tokens"):
        op.add_column(
            "llm_usage",
            sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        )
    if not _has_column("llm_usage", "cache_write_tokens"):
        op.add_column(
            "llm_usage",
            sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE usageactortype ADD VALUE IF NOT EXISTS 'compression'")


def downgrade() -> None:
    if _has_column("llm_usage", "cache_write_tokens"):
        op.drop_column("llm_usage", "cache_write_tokens")
    if _has_column("llm_usage", "cache_read_tokens"):
        op.drop_column("llm_usage", "cache_read_tokens")
