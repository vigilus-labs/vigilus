"""scope: scans, discovered hosts/services, findings; server ip+origin

Revision ID: b3f1a02c7e44
Revises: 9a2c4e70b1d8
Create Date: 2026-06-13 19:30:00.000000+00:00

Adds the Scope data model: scans + discovered hosts/services, findings, and two
additive nullable columns on ``servers`` (ip, origin) used to join discovered
hosts to the managed inventory. See SCOPE_IMPLEMENTATION_PLAN.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f1a02c7e44"
down_revision: str | None = "9a2c4e70b1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as _inspect

    inspector = _inspect(bind)

    def _has_table(name: str) -> bool:
        return name in inspector.get_table_names()

    def _has_column(table: str, col: str) -> bool:
        return col in {c["name"] for c in inspector.get_columns(table)}

    def _has_index(table: str, name: str) -> bool:
        return name in {i["name"] for i in inspector.get_indexes(table)}

    # ── scans ───────────────────────────────────────────────
    if not _has_table("scans"):
        op.create_table(
            "scans",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column(
                "source",
                sa.Enum("nmap", "wazuh", "manual", "custom", name="scopesource"),
                nullable=False,
                server_default="nmap",
            ),
            sa.Column("target", sa.String(length=512), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
            sa.Column("host_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("operator_id", sa.String(length=36), nullable=True),
            sa.Column("raw", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["operator_id"], ["operators.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    # Enum type may already exist if create_all ran with the model; create idempotently.
    sa.Enum(name="scopesource").create(bind, checkfirst=True)

    # ── discovered_hosts ────────────────────────────────────
    if not _has_table("discovered_hosts"):
        op.create_table(
            "discovered_hosts",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("scan_id", sa.String(length=36), nullable=False),
            sa.Column("ip", sa.String(length=64), nullable=False),
            sa.Column("mac", sa.String(length=64), nullable=True),
            sa.Column("hostname", sa.String(length=512), nullable=True),
            sa.Column("os_guess", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="up"),
            sa.Column("matched_server_id", sa.String(length=36), nullable=True),
            sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["matched_server_id"], ["servers.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("scan_id", "ip", name="uq_discovered_host_scan_ip"),
        )
    if not _has_index("discovered_hosts", "ix_discovered_hosts_ip"):
        op.create_index("ix_discovered_hosts_ip", "discovered_hosts", ["ip"])
    if not _has_index("discovered_hosts", "ix_discovered_hosts_matched_server_id"):
        op.create_index(
            "ix_discovered_hosts_matched_server_id", "discovered_hosts", ["matched_server_id"]
        )

    # ── discovered_services ─────────────────────────────────
    if not _has_table("discovered_services"):
        op.create_table(
            "discovered_services",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("discovered_host_id", sa.String(length=36), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False),
            sa.Column("proto", sa.String(length=16), nullable=False, server_default="tcp"),
            sa.Column("state", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column("service", sa.String(length=64), nullable=True),
            sa.Column("product", sa.String(length=255), nullable=True),
            sa.Column("version", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["discovered_host_id"], ["discovered_hosts.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("discovered_host_id", "port", "proto", name="uq_disc_service_port"),
        )

    # ── findings ────────────────────────────────────────────
    if not _has_table("findings"):
        op.create_table(
            "findings",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column(
                "source",
                sa.Enum("nmap", "wazuh", "manual", "custom", name="scopesource"),
                nullable=False,
                server_default="wazuh",
            ),
            sa.Column(
                "kind",
                sa.Enum("alert", "vulnerability", "fim", "exposure", name="findingkind"),
                nullable=False,
            ),
            sa.Column(
                "severity",
                sa.Enum("info", "low", "medium", "high", "critical", name="findingseverity"),
                nullable=False,
                server_default="info",
            ),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("detail", sa.JSON(), nullable=True),
            sa.Column("server_id", sa.String(length=36), nullable=True),
            sa.Column("discovered_host_id", sa.String(length=36), nullable=True),
            sa.Column("host_identifier", sa.String(length=255), nullable=True),
            sa.Column("fingerprint", sa.String(length=255), nullable=True),
            sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["discovered_host_id"], ["discovered_hosts.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("fingerprint", name="uq_finding_fingerprint"),
        )
    sa.Enum(name="findingkind").create(bind, checkfirst=True)
    sa.Enum(name="findingseverity").create(bind, checkfirst=True)
    if not _has_index("findings", "ix_findings_server_id"):
        op.create_index("ix_findings_server_id", "findings", ["server_id"])
    if not _has_index("findings", "ix_findings_discovered_host_id"):
        op.create_index("ix_findings_discovered_host_id", "findings", ["discovered_host_id"])
    if not _has_index("findings", "ix_findings_fingerprint"):
        op.create_index("ix_findings_fingerprint", "findings", ["fingerprint"])

    # ── server additions (the column adds that actually fix the 500) ───
    if not _has_column("servers", "ip"):
        op.add_column("servers", sa.Column("ip", sa.String(length=64), nullable=True))
    if not _has_column("servers", "origin"):
        op.add_column("servers", sa.Column("origin", sa.String(length=32), nullable=True))
    if not _has_index("servers", "ix_servers_ip"):
        op.create_index("ix_servers_ip", "servers", ["ip"])


def downgrade() -> None:
    op.drop_index("ix_servers_ip", table_name="servers")
    op.drop_column("servers", "origin")
    op.drop_column("servers", "ip")

    op.drop_index("ix_findings_fingerprint", table_name="findings")
    op.drop_index("ix_findings_discovered_host_id", table_name="findings")
    op.drop_index("ix_findings_server_id", table_name="findings")
    op.drop_table("findings")

    op.drop_table("discovered_services")

    op.drop_index("ix_discovered_hosts_matched_server_id", table_name="discovered_hosts")
    op.drop_index("ix_discovered_hosts_ip", table_name="discovered_hosts")
    op.drop_table("discovered_hosts")

    op.drop_table("scans")

    # Drop the enum types we created (Postgres only; SQLite ignores these).
    sa_enum_source = sa.Enum(name="scopesource")
    sa_enum_source.drop(op.get_bind(), checkfirst=True)
    sa_enum_kind = sa.Enum(name="findingkind")
    sa_enum_kind.drop(op.get_bind(), checkfirst=True)
    sa_enum_sev = sa.Enum(name="findingseverity")
    sa_enum_sev.drop(op.get_bind(), checkfirst=True)
