"""Add ssh_auth_method to Credential

Revision ID: b7c2f1038a02
Revises: af98e9057b01
Create Date: 2026-06-11 22:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c2f1038a02"
down_revision: str | None = "af98e9057b01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "credentials",
        sa.Column(
            "ssh_auth_method", sa.Enum("key", "password", name="sshauthmethod"), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("credentials", "ssh_auth_method")
