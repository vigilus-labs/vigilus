"""context window, schedule retries, and scheduler lease

Revision ID: d5f9b2c3e4a1
Revises: c4e8a1b2d390
Create Date: 2026-09-25 14:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5f9b2c3e4a1"
down_revision: str | None = "c4e8a1b2d390"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in {c["name"] for c in inspector.get_columns(table)}


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_column("providers", "context_window"):
        op.add_column("providers", sa.Column("context_window", sa.Integer(), nullable=True))
    if not _has_column("scheduled_tasks", "max_attempts"):
        op.add_column(
            "scheduled_tasks",
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        )
    if not _has_column("scheduled_tasks", "retry_backoff_seconds"):
        op.add_column(
            "scheduled_tasks",
            sa.Column("retry_backoff_seconds", sa.Integer(), nullable=False, server_default="30"),
        )
    if not _has_table("scheduler_lease"):
        op.create_table(
            "scheduler_lease",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("holder", sa.String(length=64), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.bulk_insert(
            sa.table(
                "scheduler_lease",
                sa.column("id", sa.String),
                sa.column("holder", sa.String),
                sa.column("expires_at", sa.DateTime),
            ),
            [{"id": "leader", "holder": None, "expires_at": None}],
        )


def downgrade() -> None:
    if _has_table("scheduler_lease"):
        op.drop_table("scheduler_lease")
    if _has_column("scheduled_tasks", "retry_backoff_seconds"):
        op.drop_column("scheduled_tasks", "retry_backoff_seconds")
    if _has_column("scheduled_tasks", "max_attempts"):
        op.drop_column("scheduled_tasks", "max_attempts")
    if _has_column("providers", "context_window"):
        op.drop_column("providers", "context_window")
