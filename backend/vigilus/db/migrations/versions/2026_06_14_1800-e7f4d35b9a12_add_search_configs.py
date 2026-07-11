"""add search_configs

Revision ID: e7f4d35b9a12
Revises: d6e3c24a9f01
Create Date: 2026-06-14 18:00:00.000000+00:00

Adds the ``search_configs`` table backing Vigilus-only web research
(SEARCH_IMPLEMENTATION_PLAN.md §4.2). Single-row config: which search/fetch
backends are active, the SearXNG URL, and the Fernet-encrypted Firecrawl API
key. Guarded/idempotent like the other migrations.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f4d35b9a12"
down_revision: str | None = "d6e3c24a9f01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(bind, name: str) -> bool:
    from sqlalchemy import inspect as _inspect

    return name in _inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "search_configs"):
        op.create_table(
            "search_configs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column(
                "search_backend", sa.String(length=32), nullable=False, server_default="searxng"
            ),
            sa.Column(
                "fetch_backend", sa.String(length=32), nullable=False, server_default="builtin"
            ),
            sa.Column("searxng_url", sa.String(length=1024), nullable=True),
            sa.Column("firecrawl_api_key_enc", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "search_configs"):
        op.drop_table("search_configs")
