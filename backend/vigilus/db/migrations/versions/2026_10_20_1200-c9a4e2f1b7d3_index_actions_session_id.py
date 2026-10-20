"""index actions.session_id

Revision ID: c9a4e2f1b7d3
Revises: b4e8c1d92a07
Create Date: 2026-10-20 12:00:00.000000+00:00

The chat work trace reads a session's audit rows every time the session is
opened. Without an index that is a scan of the whole audit log.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9a4e2f1b7d3"
down_revision: str | None = "b4e8c1d92a07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_actions_session_id"


def _has_index(table: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return name in {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_index("actions", _INDEX):
        op.create_index(_INDEX, "actions", ["session_id"])


def downgrade() -> None:
    if _has_index("actions", _INDEX):
        op.drop_index(_INDEX, table_name="actions")
