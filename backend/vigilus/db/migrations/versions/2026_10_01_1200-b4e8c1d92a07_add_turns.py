"""durable turn checkpoints

Revision ID: b4e8c1d92a07
Revises: e7b2c3d4f5a6
Create Date: 2026-10-01 12:00:00.000000+00:00

Adds the ``turns`` table. A strict-mode JIT wait parks a row here instead of
holding a coroutine, and approval resumes from the checkpoint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e8c1d92a07"
down_revision: str | None = "e7b2c3d4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


_TURN_STATUS = sa.Enum(
    "running",
    "awaiting_approval",
    "completed",
    "failed",
    "cancelled",
    name="turnstatus",
)


def upgrade() -> None:
    if _has_table("turns"):
        return
    bind = op.get_bind()
    _TURN_STATUS.create(bind, checkfirst=True)
    op.create_table(
        "turns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("status", _TURN_STATUS, nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=True),
        sa.Column("operator_id", sa.String(length=36), nullable=True),
        sa.Column("pending_call", sa.JSON(), nullable=True),
        sa.Column("operator_messages", sa.JSON(), nullable=True),
        sa.Column("jit_request_id", sa.String(length=36), nullable=True),
        sa.Column("deliver_to", sa.JSON(), nullable=True),
        sa.Column("unattended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["operators.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_turns_session_id", "turns", ["session_id"])
    op.create_index("ix_turns_jit_request_id", "turns", ["jit_request_id"])


def downgrade() -> None:
    if not _has_table("turns"):
        return
    op.drop_index("ix_turns_jit_request_id", table_name="turns")
    op.drop_index("ix_turns_session_id", table_name="turns")
    op.drop_table("turns")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _TURN_STATUS.drop(bind, checkfirst=True)
