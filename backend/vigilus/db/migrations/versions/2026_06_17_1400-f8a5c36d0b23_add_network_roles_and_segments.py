"""add network_roles and network_segments

Revision ID: f8a5c36d0b23
Revises: e7f4d35b9a12
Create Date: 2026-06-17 14:00:00.000000+00:00

Adds the ``network_roles`` and ``network_segments`` tables backing the Scope
topology redesign (SCOPE_TOPOLOGY_IMPLEMENTATION_PLAN.md §1). ``network_roles``
stores manual per-IP role tagging (gateway/dns/switch/access_point); it is keyed
by IP rather than host id so the tag survives across rescans and managed/
discovered identity merges. ``network_segments`` stores optional cosmetic
label/color overrides for subnet (VLAN-proxy) groupings, which are otherwise
computed at request time from ``Scan.target``. Guarded/idempotent like the other
migrations.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a5c36d0b23"
down_revision: str | None = "e7f4d35b9a12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(bind, name: str) -> bool:
    from sqlalchemy import inspect as _inspect

    return name in _inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "network_roles"):
        op.create_table(
            "network_roles",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("ip", sa.String(length=64), nullable=False),
            sa.Column("is_gateway", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("is_dns", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("is_switch", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("is_access_point", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("label", sa.String(length=255), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("ip", name="uq_network_roles_ip"),
        )
        op.create_index("ix_network_roles_ip", "network_roles", ["ip"])

    if not _has_table(bind, "network_segments"):
        op.create_table(
            "network_segments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("cidr", sa.String(length=64), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=True),
            sa.Column("color", sa.String(length=32), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("cidr", name="uq_network_segments_cidr"),
        )
        op.create_index("ix_network_segments_cidr", "network_segments", ["cidr"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "network_segments"):
        op.drop_table("network_segments")
    if _has_table(bind, "network_roles"):
        op.drop_table("network_roles")
