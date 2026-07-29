"""add llm_usage

Revision ID: a1b2c3d4e5f6
Revises: f8a5c36d0b23
Create Date: 2026-07-29 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f8a5c36d0b23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as _inspect

    inspector = _inspect(bind)

    def _has_table(name: str) -> bool:
        return name in inspector.get_table_names()

    def _has_index(table: str, name: str) -> bool:
        return name in {i["name"] for i in inspector.get_indexes(table)}

    if not _has_table("llm_usage"):
        op.create_table(
            "llm_usage",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "actor_type",
                sa.Enum("orchestrator", "operator", name="usageactortype"),
                nullable=False,
            ),
            sa.Column("operator_id", sa.String(length=36), nullable=True),
            sa.Column("session_id", sa.String(length=36), nullable=True),
            sa.Column("provider_id", sa.String(length=36), nullable=True),
            sa.Column("provider_type", sa.String(length=64), nullable=True),
            sa.Column("model", sa.String(length=255), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=False),
            sa.Column("output_tokens", sa.Integer(), nullable=False),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["operator_id"], ["operators.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
    # Enum may already exist if create_all ran with the model; create idempotently.
    sa.Enum("orchestrator", "operator", name="usageactortype").create(bind, checkfirst=True)

    if not _has_index("llm_usage", "ix_llm_usage_created_at"):
        op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    if not _has_index("llm_usage", "ix_llm_usage_actor_type"):
        op.create_index("ix_llm_usage_actor_type", "llm_usage", ["actor_type"])
    if not _has_index("llm_usage", "ix_llm_usage_provider_type"):
        op.create_index("ix_llm_usage_provider_type", "llm_usage", ["provider_type"])
    if not _has_index("llm_usage", "ix_llm_usage_actor_created"):
        op.create_index(
            "ix_llm_usage_actor_created",
            "llm_usage",
            ["actor_type", "operator_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as _inspect

    if "llm_usage" not in _inspect(bind).get_table_names():
        return
    op.drop_index("ix_llm_usage_actor_created", table_name="llm_usage")
    op.drop_index("ix_llm_usage_provider_type", table_name="llm_usage")
    op.drop_index("ix_llm_usage_actor_type", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
