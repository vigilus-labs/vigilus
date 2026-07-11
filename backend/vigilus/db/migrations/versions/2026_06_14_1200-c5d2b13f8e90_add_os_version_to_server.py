"""add os_version to servers

Revision ID: c5d2b13f8e90
Revises: b3f1a02c7e44
Create Date: 2026-06-14 12:00:00.000000+00:00

Adds an additive nullable ``os_version`` column to ``servers`` so OS type
(existing ``os``) and version can be tracked separately. Both are optional and
can be auto-filled when a scan matches the host.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d2b13f8e90"
down_revision: str | None = "b3f1a02c7e44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as _inspect

    inspector = _inspect(bind)

    def _has_column(table: str, col: str) -> bool:
        return col in {c["name"] for c in inspector.get_columns(table)}

    if not _has_column("servers", "os_version"):
        op.add_column("servers", sa.Column("os_version", sa.String(length=255), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as _inspect

    inspector = _inspect(bind)

    def _has_column(table: str, col: str) -> bool:
        return col in {c["name"] for c in inspector.get_columns(table)}

    if _has_column("servers", "os_version"):
        op.drop_column("servers", "os_version")
