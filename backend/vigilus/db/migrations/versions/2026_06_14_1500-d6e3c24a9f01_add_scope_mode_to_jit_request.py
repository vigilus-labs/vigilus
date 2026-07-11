"""add scope_mode to jit_requests

Revision ID: d6e3c24a9f01
Revises: c5d2b13f8e90
Create Date: 2026-06-14 15:00:00.000000+00:00

Adds ``scope_mode`` to ``jit_requests`` so an approval can be single-use
("once") or reusable for its TTL ("timed"). Existing rows default to "timed",
matching prior behavior.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e3c24a9f01"
down_revision: str | None = "c5d2b13f8e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as _inspect

    inspector = _inspect(bind)

    def _has_column(table: str, col: str) -> bool:
        return col in {c["name"] for c in inspector.get_columns(table)}

    if not _has_column("jit_requests", "scope_mode"):
        op.add_column(
            "jit_requests",
            sa.Column("scope_mode", sa.String(length=16), nullable=False, server_default="timed"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as _inspect

    inspector = _inspect(bind)

    def _has_column(table: str, col: str) -> bool:
        return col in {c["name"] for c in inspector.get_columns(table)}

    if _has_column("jit_requests", "scope_mode"):
        op.drop_column("jit_requests", "scope_mode")
